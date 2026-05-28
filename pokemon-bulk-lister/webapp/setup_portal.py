"""One-time login capture for TCGPlayer / Whatnot seller portals.

Run from your terminal (NOT from inside the Flask app):

    cd pokemon-bulk-lister
    python -m webapp.setup_portal --site tcgplayer
    python -m webapp.setup_portal --site whatnot

A real Chromium window opens at the portal's login page. Sign in (and clear any
2FA), navigate to the seller area, then press Enter back here to save the
session. The headless CSV uploader then reuses that session.

⚠️ Automating these portals is generally against their Terms — see
lib/portal_uploader.py. Use a personal/secondary account at your own risk.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

SITES = {
    "tcgplayer": {
        "login_url": "https://store.tcgplayer.com/login",
        "default_state": "output/cache/tcgplayer_state.json",
        "state_env": "TCGPLAYER_STATE_PATH",
    },
    "whatnot": {
        "login_url": "https://www.whatnot.com/login",
        "default_state": "output/cache/whatnot_state.json",
        "state_env": "WHATNOT_STATE_PATH",
    },
}


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, choices=sorted(SITES.keys()))
    args = parser.parse_args()

    import os

    cfg = SITES[args.site]
    state_path = Path(os.getenv(cfg["state_env"], cfg["default_state"]))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        print(f"NOTE: existing session at {state_path} — it'll be overwritten if you proceed.")

    print()
    print(f"=== {args.site} login ===")
    print(f"1. A Chromium window will open at {cfg['login_url']}.")
    print("2. Sign in (clear any 2FA / captcha) and reach the seller dashboard.")
    print("3. Come back here and press Enter to save the session.")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-default-browser-check",
                "--no-first-run",
            ],
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
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "window.chrome = {runtime: {}};"
        )
        page = context.new_page()
        try:
            page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"failed to load {cfg['login_url']}: {exc}", file=sys.stderr)
            print("The browser is still open — navigate manually, then continue.")

        try:
            input("Press Enter once you're signed in and on the seller dashboard… ")
        except KeyboardInterrupt:
            print("\naborted — nothing saved.")
            browser.close()
            return 1

        try:
            context.storage_state(path=str(state_path))
        except Exception as exc:
            print(f"failed to save session: {exc}", file=sys.stderr)
            browser.close()
            return 1
        browser.close()

    print(f"\nSaved {args.site} session to {state_path}")
    print("The headless CSV uploader will now reuse this session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
