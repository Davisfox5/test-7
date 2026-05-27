"""Standalone Terapeak login helper.

Run from your terminal (NOT from inside the Flask app):

    cd pokemon-bulk-lister
    python -m webapp.setup_terapeak

A real Chromium window will open. Sign in to eBay, complete any 2FA, then
navigate to Seller Hub -> Research. This script polls the page URL and
automatically saves the session once it detects you're on Terapeak Research.

After that the web app's 'Use Terapeak' checkbox will work headlessly.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_STATE_PATH = "output/cache/terapeak_state.json"
TERAPEAK_URL = "https://www.ebay.com/sh/research"
SUCCESS_TOKEN = "sh/research"
TIMEOUT_SECONDS = 900   # 15 min hard cap — the script keeps watching past this too


def main() -> int:
    state_path = Path(os.getenv("TERAPEAK_STATE_PATH", DEFAULT_STATE_PATH))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        print(f"NOTE: existing session at {state_path} — it'll be overwritten if you proceed.")

    print()
    print("=== Terapeak login ===")
    print("1. A Chromium window will open at eBay's Seller Hub Research page.")
    print("2. Sign in to eBay (and clear any 2FA / captcha).")
    print("3. Once Seller Hub Research loads, this script will detect it and save the session.")
    print("4. You can also press Ctrl-C here to abort.")
    print()

    debug_dir = Path("output/cache/terapeak_setup_debug")
    debug_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # Anti-bot flags: hide Playwright's automation fingerprint so eBay's
        # splashui/captcha doesn't lock us into an un-solvable challenge.
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            # ignore_default_args=["--enable-automation"] would also help but
            # we need it carefully; instead we patch the webdriver flag below.
            ignore_default_args=["--enable-automation"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.7632.6 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/Los_Angeles",
        )
        # Mask navigator.webdriver and other obvious tells.
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "window.chrome = {runtime: {}};"
        )
        page = context.new_page()
        try:
            page.goto(TERAPEAK_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"failed to load {TERAPEAK_URL}: {exc}", file=sys.stderr)
            print("The browser is still open — try navigating manually. We'll keep polling.")

        start = time.time()
        last_url = None
        last_shot = 0.0
        while True:
            try:
                url = page.url
            except Exception:
                print("Browser window closed before login completed. Nothing was saved.")
                return 1
            if url != last_url:
                print(f"   on: {url}", flush=True)
                last_url = url
                # Snap a screenshot any time the URL changes so we can see
                # captchas / error pages from outside the browser.
                try:
                    ts = int(time.time())
                    page.screenshot(path=str(debug_dir / f"{ts}.png"), full_page=False)
                except Exception:
                    pass
                last_shot = time.time()
            # Also snap one every 15s even if URL is stable (captcha shifts).
            if time.time() - last_shot > 15:
                try:
                    ts = int(time.time())
                    page.screenshot(path=str(debug_dir / f"{ts}-poll.png"), full_page=False)
                except Exception:
                    pass
                last_shot = time.time()
            if SUCCESS_TOKEN in url:
                time.sleep(2)
                break
            elapsed = time.time() - start
            if elapsed > TIMEOUT_SECONDS:
                print(f"Still not at Seller Hub Research after {TIMEOUT_SECONDS}s. Keeping browser open; press Ctrl-C to abort.", flush=True)
                start = time.time()
            time.sleep(1)

        context.storage_state(path=str(state_path))
        browser.close()

    print(f"\nSaved Terapeak session to {state_path}")
    print("The web app's 'Use Terapeak' checkbox will now work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
