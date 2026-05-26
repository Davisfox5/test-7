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

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            page.goto(TERAPEAK_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"failed to load {TERAPEAK_URL}: {exc}", file=sys.stderr)
            print("The browser is still open — try navigating manually. We'll keep polling.")

        start = time.time()
        last_url = None
        while True:
            try:
                url = page.url
            except Exception:
                # Page got closed; the user shut the window.
                print("Browser window closed before login completed. Nothing was saved.")
                return 1
            if url != last_url:
                print(f"   on: {url}")
                last_url = url
            if SUCCESS_TOKEN in url:
                # Wait a beat for any async cookies to settle, then save.
                time.sleep(2)
                break
            elapsed = time.time() - start
            if elapsed > TIMEOUT_SECONDS:
                print(f"Still not at Seller Hub Research after {TIMEOUT_SECONDS}s. Keeping browser open; press Ctrl-C to abort.")
                start = time.time()  # reset so we don't spam this message
            time.sleep(1)

        context.storage_state(path=str(state_path))
        browser.close()

    print(f"\nSaved Terapeak session to {state_path}")
    print("The web app's 'Use Terapeak' checkbox will now work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
