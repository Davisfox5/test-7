"""Thread-pooled Terapeak client.

Each worker thread owns its own ``TerapeakClient`` (and therefore its own
Chromium browser process). Workers share the saved login session via
storage_state on disk, so eBay sees N tabs of one user — normal behavior.

Threading is the right concurrency model here because Playwright's *sync*
API isn't safe to call across threads from one instance, but is fine if each
thread has its own ``sync_playwright()`` lifetime. The async API would also
work but would require rewriting the existing scrape code.

Tuning notes:
  * 4 workers ≈ 600 MB browser RAM, ~12 req/min to eBay total. Well under
    the captcha threshold (~30 req/min/session).
  * Higher counts risk session invalidation when eBay's bot scoring kicks
    in. Don't exceed 6 without re-testing.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable, Optional

from lib.terapeak_client import TerapeakClient


class TerapeakPool:
    def __init__(self, n_workers: int = 4, **client_kwargs) -> None:
        self.n_workers = n_workers
        self._client_kwargs = client_kwargs
        self._jobs: "queue.Queue[tuple[str, int, Future]]" = queue.Queue()
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        # Lazily start workers on first submit, so a quick "did it import"
        # check doesn't spin up Chromium.
        self._started = False
        self._lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            for i in range(self.n_workers):
                t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
                t.start()
                self._workers.append(t)
            self._started = True

    def _worker_loop(self, worker_id: int) -> None:
        client: Optional[TerapeakClient] = None
        try:
            client = TerapeakClient(**self._client_kwargs)
        except Exception as exc:
            # Drain the queue so callers don't block forever
            while not self._stop.is_set():
                try:
                    query, days, fut = self._jobs.get(timeout=0.5)
                except queue.Empty:
                    continue
                fut.set_exception(exc)
                self._jobs.task_done()
            return

        try:
            while not self._stop.is_set():
                try:
                    query, days, fut = self._jobs.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    result = client.search(query, days=days)
                    fut.set_result(result)
                except Exception as exc:
                    fut.set_exception(exc)
                finally:
                    self._jobs.task_done()
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def submit(self, query: str, days: int = 365) -> Future:
        self._ensure_started()
        fut: Future = Future()
        self._jobs.put((query, days, fut))
        return fut

    def search_many(
        self,
        queries: list[tuple[str, int]],
        on_each: Optional[Callable[[int, str, Any], None]] = None,
    ) -> list[Any]:
        """Submit all queries, return results in input order.

        ``on_each`` (if provided) is called with (index, query, result_or_exc)
        as each query completes — useful for progress reporting.
        """
        futures = [self.submit(q, d) for q, d in queries]
        results: list[Any] = [None] * len(futures)
        for i, fut in enumerate(futures):
            try:
                results[i] = fut.result()
            except Exception as exc:
                results[i] = exc
            if on_each is not None:
                try:
                    on_each(i, queries[i][0], results[i])
                except Exception:
                    pass
        return results

    def close(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=10)
