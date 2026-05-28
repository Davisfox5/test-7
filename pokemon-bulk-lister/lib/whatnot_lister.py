"""Whatnot Seller Hub inventory-import CSV upload via headless browser.

Whatnot's Seller API is invite/partner-only, so for a typical seller the
inventory-import CSV in Seller Hub is the way in — exactly what
``scripts/05_generate_csvs.whatnot_rows`` produces. This driver attaches that
CSV to the Seller Hub import form.

See ``lib/portal_uploader.py`` for the TOS/fragility caveats.

Override the upload URL / selectors via env if the portal layout shifts:
  WHATNOT_UPLOAD_URL, WHATNOT_STATE_PATH, WHATNOT_HEADLESS
"""
from __future__ import annotations

import os

from lib.portal_uploader import PortalUploader


class WhatnotLister(PortalUploader):
    site_name = "whatnot"
    upload_url = os.getenv(
        "WHATNOT_UPLOAD_URL",
        "https://www.whatnot.com/seller/inventory/import",
    )
    file_input_selectors = (
        'input[type="file"][accept*="csv" i]',
        'input[type="file"]',
    )
    submit_selectors = (
        'button:has-text("Import")',
        'button:has-text("Upload")',
        'button:has-text("Continue")',
        'button[type="submit"]',
    )
    login_redirect_token = "login"
    success_text = ("imported", "success", "uploaded", "added")
