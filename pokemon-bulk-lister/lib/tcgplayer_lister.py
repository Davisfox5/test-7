"""TCGPlayer staged-inventory CSV upload via headless browser.

TCGPlayer's developer API is partner-gated and offers no general listing
endpoint, so the supported seller path is the Seller Portal's staged-inventory
bulk CSV — exactly what ``scripts/05_generate_csvs.tcgplayer_rows`` produces.
This driver attaches that CSV to the portal's add-inventory uploader.

See ``lib/portal_uploader.py`` for the TOS/fragility caveats.

Override the upload URL / selectors via env if the portal layout shifts:
  TCGPLAYER_UPLOAD_URL, TCGPLAYER_STATE_PATH, TCGPLAYER_HEADLESS
"""
from __future__ import annotations

import os

from lib.portal_uploader import PortalUploader


class TCGPlayerLister(PortalUploader):
    site_name = "tcgplayer"
    upload_url = os.getenv(
        "TCGPLAYER_UPLOAD_URL",
        "https://store.tcgplayer.com/admin/Product/StagedInventory",
    )
    file_input_selectors = (
        'input[type="file"][accept*="csv" i]',
        'input[type="file"]',
    )
    submit_selectors = (
        'button:has-text("Upload")',
        'button:has-text("Stage")',
        'button:has-text("Import")',
        'input[type="submit"]',
        'button[type="submit"]',
    )
    login_redirect_token = "login"
    success_text = ("staged", "success", "uploaded", "imported")
