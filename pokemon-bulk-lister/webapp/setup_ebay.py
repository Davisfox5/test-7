"""One-time eBay listing authorization.

Run from your terminal (NOT from inside the Flask app):

    cd pokemon-bulk-lister
    python -m webapp.setup_ebay

This walks the eBay authorization-code consent flow:
  1. Prints (and tries to open) a consent URL.
  2. You sign in to eBay and approve the listing scopes.
  3. eBay redirects your browser to your RuName's "accepted" URL with ?code=...
  4. Paste that full redirected URL back here.
  5. The refresh token is exchanged and cached; listing then works headlessly.

Prereqs in .env:
  EBAY_CLIENT_ID, EBAY_CLIENT_SECRET   (Production keyset)
  EBAY_REDIRECT_URI                    (your RuName, created in the dev portal)
"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from lib.ebay_oauth import EbayUserAuth, EbayUserAuthError  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    try:
        auth = EbayUserAuth()
        url = auth.build_consent_url()
    except EbayUserAuthError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 1

    print()
    print("=== eBay listing authorization ===")
    print("1. Open this URL and sign in / approve:")
    print()
    print(f"   {url}")
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("2. After approving, eBay redirects to your RuName's accepted URL.")
    print("   Copy the FULL redirected URL from the browser address bar.")
    print()
    redirect = input("Paste the redirected URL (or just the code): ").strip()
    if not redirect:
        print("nothing pasted — aborting.", file=sys.stderr)
        return 1

    try:
        code = EbayUserAuth.extract_code(redirect)
        auth.exchange_code(code)
    except EbayUserAuthError as exc:
        print(f"\nfailed: {exc}", file=sys.stderr)
        return 1

    print(f"\nSaved eBay refresh token to {auth.token_path}")
    print("Live eBay listing is now enabled (publish from the web UI or scripts/07_publish_listings.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
