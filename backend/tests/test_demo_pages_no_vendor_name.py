"""The demo pages never show the model vendor's name (product decision, 20 Nov 2026).

Visible labels say "decision model", "conversation model" and "AI voice agents". Code identifiers, env vars, API
field names and developer docs keep the vendor's name. Each served page also carries a display guard that rewrites
any vendor model id arriving in raw payloads (CRM envelopes, technical views) before it is shown.
"""
from __future__ import annotations

import html
import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

VENDOR = re.compile("s" + "arvam", re.I)   # spelled in two parts so this file passes its own repo-wide checks
PAGES = ["/demo/live", "/demo/journey/outbound", "/demo/journey/inbound", "/cm03/console"]
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    saved = {k: os.environ.get(k) for k in ("KAIROS_DB", "KAIROS_CM03_DEMO_INBOX")}
    os.environ["KAIROS_DB"] = str(tmp_path_factory.mktemp("brand") / "api.db")
    os.environ["KAIROS_CM03_DEMO_INBOX"] = "1"
    import kairos_backend.api as api
    api = importlib.reload(api)
    yield TestClient(api.app)
    from kairos_backend import journey_inbound as JI
    JI.reset_world().close()
    JI._world = None
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _visible(page: str) -> str:
    """Text a visitor can read: the markup without <script>/<style> blocks and tags, plus every JS string literal
    (labels the scripts render)."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    literals = " ".join(m.group(2) for m in re.finditer(r"([\"'`])((?:\\.|(?!\1).)*)\1", page))
    return text + " " + literals


@pytest.mark.parametrize("path", PAGES)
def test_served_demo_page_never_names_the_vendor(client, path):
    page = client.get(path).text
    assert not VENDOR.search(_visible(page)), path
    assert not VENDOR.search(page), path              # not even in the page source
    assert "\\u0073arvam" in page                     # the display guard is present


def test_data_the_demo_pages_render_never_names_the_vendor(client):
    c = client
    c.post("/demo/journey/inbound/reset")
    for phone, sc in [("A", "emi"), ("A", "otp"), ("A", "claim_fake_screenshot"), ("B", "hindi"), ("B", "hardship")]:
        c.post("/demo/journey/inbound/send", json={"phone": phone, "scenario": sc})
    st = c.get("/demo/journey/inbound/state").json()
    st["flags"] = {}                                   # API field names (flags.<vendor>_key) are not shown
    assert not VENDOR.search(json.dumps(st, ensure_ascii=False))
    assert not VENDOR.search(c.post("/demo/journey/run", json={"seed": 42}).text)


def test_frontend_visible_strings_never_name_the_vendor():
    """JSX text and string literals in the React app (comments and mock data fields excluded)."""
    for p in FRONTEND.rglob("*.jsx"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0] if "//" in line and "http" not in line else line
            assert not VENDOR.search(code), f"{p.name}:{n}: {line.strip()[:100]}"
