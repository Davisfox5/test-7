"""Shared headless-browser base for seller-portal CSV uploads.

TCGPlayer and Whatnot have no public listing API for the typical seller, so the
only way to automate inventory is to drive their Seller Portal in a browser the
way a person would: log in once (session saved to disk), then attach the bulk
CSV this app already generates and submit it.

⚠️ This is browser automation of sites whose Terms generally prohibit it. It is
fragile (their DOM changes without notice) and use is at your own risk — keep it
to a personal/secondary account. The official, supported alternative is to just
upload the CSVs by hand; this module only saves the clicks.

Design mirrors ``lib/terapeak_client.py``: same stealth flags, the same
storage_state session reuse, and the same defensive "try several selectors then
dump a screenshot + HTML on failure" parsing so selectors can be tuned without
re-running the whole pipeline. Subclasses supply the portal URL and the selector
candidates for the file input and submit control.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        TimeoutError as PWTimeoutError,
        sync_playwright,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Portal CSV upload requires playwright. Install with:\n"
        "  pip install playwright && playwright install chromium"
    ) from exc


_STEALTH_INIT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
    "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
    "window.chrome = {runtime: {}};"
)


class PortalNotLoggedIn(RuntimeError):
    """Raised when no saved session exists for a portal."""


class PortalUploadError(RuntimeError):
    pass


class PortalUploader:
    """Base class. Subclass and set the class attributes below."""

    site_name: str = "portal"
    upload_url: str = ""
    # Selector candidates tried in order until one matches.
    file_input_selectors: tuple[str, ...] = ('input[type="file"]',)
    submit_selectors: tuple[str, ...] = (
        'button:has-text("Upload")',
        'button:has-text("Import")',
        'button:has-text("Submit")',
        'button[type="submit"]',
    )
    # A substring that, if present in the URL after navigation, means the saved
    # session is dead and we got bounced to a login page.
    login_redirect_token: str = "signin"
    # A substring expected somewhere in the page text on a successful upload.
    success_text: tuple[str, ...] = ("success", "uploaded", "imported", "complete")

    def __init__(
        self,
        state_path: Optional[str] = None,
        headless: Optional[bool] = None,
    ) -> None:
        self.state_path = Path(state_path or self._default_state_path())
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        env_headless = os.getenv(f"{self.site_name.upper()}_HEADLESS", "1")
        self.headless = headless if headless is not None else env_headless == "1"
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._ensure_logged_in()

    @classmethod
    def _default_state_path(cls) -> str:
        return os.getenv(
            f"{cls.site_name.upper()}_STATE_PATH",
            f"output/cache/{cls.site_name}_state.json",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_logged_in(self) -> None:
        if self.state_path.exists():
            return
        raise PortalNotLoggedIn(
            f"No {self.site_name} session at {self.state_path}. Run from your terminal:\n"
            f"    python -m webapp.setup_portal --site {self.site_name}\n"
            "A Chromium window will open; sign in and the session will be saved."
        )

    def _start_browser(self) -> None:
        if self._pw is not None:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            ignore_default_args=["--enable-automation"],
        )

    def _new_context(self) -> BrowserContext:
        self._start_browser()
        assert self._browser is not None
        ctx = self._browser.new_context(
            storage_state=str(self.state_path),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.7632.6 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/Los_Angeles",
            accept_downloads=True,
        )
        ctx.add_init_script(_STEALTH_INIT)
        return ctx

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def __enter__(self) -> "PortalUploader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_csv(self, csv_path: str) -> dict:
        """Attach ``csv_path`` to the portal's bulk-import form and submit.

        Returns ``{"ok": bool, "detail": str}``. Subclasses can override
        ``_after_attach`` to handle portal-specific confirmation steps.
        """
        csv_file = Path(csv_path)
        if not csv_file.exists():
            raise PortalUploadError(f"CSV not found: {csv_path}")

        context = self._new_context()
        page = context.new_page()
        try:
            return self._do_upload(page, csv_file)
        finally:
            try:
                context.close()
            except Exception:
                pass

    def _do_upload(self, page: Page, csv_file: Path) -> dict:
        page.goto(self.upload_url, wait_until="domcontentloaded", timeout=60000)
        if self.login_redirect_token and self.login_redirect_token in page.url:
            raise PortalUploadError(
                f"{self.site_name} bounced to {page.url} — saved session expired. "
                f"Delete {self.state_path} and rerun python -m webapp.setup_portal "
                f"--site {self.site_name}."
            )

        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeoutError:
            pass

        file_input = self._find_first(page, self.file_input_selectors, want_visible=False)
        if file_input is None:
            self._dump_debug(page, "no-file-input")
            raise PortalUploadError(
                f"could not find a file input on {self.site_name}'s upload page. "
                f"Debug screenshot+HTML written to output/cache/{self.site_name}_debug/. "
                "The portal's DOM likely changed — update the selectors."
            )

        file_input.set_input_files(str(csv_file))
        self._after_attach(page)

        submit = self._find_first(page, self.submit_selectors, want_visible=True)
        if submit is not None:
            try:
                submit.click()
            except Exception:
                pass

        # Give the upload a chance to process.
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeoutError:
            pass
        time.sleep(2)

        body = ""
        try:
            body = page.inner_text("body").lower()
        except Exception:
            pass
        ok = any(tok in body for tok in self.success_text)
        if not ok:
            self._dump_debug(page, "post-submit")
        return {
            "ok": ok,
            "detail": (
                "submitted; success text detected"
                if ok
                else f"submitted but no success confirmation found — see output/cache/{self.site_name}_debug/"
            ),
        }

    def _after_attach(self, page: Page) -> None:
        """Hook for portal-specific steps between attaching the file and submit."""
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_first(self, page: Page, selectors: tuple[str, ...], want_visible: bool):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if want_visible:
                    loc.wait_for(state="visible", timeout=6000)
                else:
                    loc.wait_for(state="attached", timeout=6000)
                return loc
            except PWTimeoutError:
                continue
            except Exception:
                continue
        return None

    def _dump_debug(self, page: Page, tag: str) -> None:
        debug_dir = Path(f"output/cache/{self.site_name}_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        try:
            page.screenshot(path=str(debug_dir / f"{ts}-{tag}.png"), full_page=True)
        except Exception:
            pass
        try:
            (debug_dir / f"{ts}-{tag}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
