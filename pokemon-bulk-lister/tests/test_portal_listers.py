"""Portal uploader config + guards (Playwright stubbed in conftest)."""
from __future__ import annotations

import pytest

from lib.portal_uploader import PortalNotLoggedIn
from lib.tcgplayer_lister import TCGPlayerLister
from lib.whatnot_lister import WhatnotLister


def test_tcgplayer_config():
    assert TCGPlayerLister.site_name == "tcgplayer"
    assert "tcgplayer.com" in TCGPlayerLister.upload_url
    assert TCGPlayerLister._default_state_path() == "output/cache/tcgplayer_state.json"
    assert 'input[type="file"]' in TCGPlayerLister.file_input_selectors[-1]


def test_whatnot_config():
    assert WhatnotLister.site_name == "whatnot"
    assert "whatnot.com" in WhatnotLister.upload_url
    assert WhatnotLister._default_state_path() == "output/cache/whatnot_state.json"


def test_not_logged_in_raises_with_guidance(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(PortalNotLoggedIn, match="setup_portal --site tcgplayer"):
        TCGPlayerLister(state_path=str(missing))


def test_existing_session_constructs(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    inst = WhatnotLister(state_path=str(state))
    assert str(inst.state_path) == str(state)
