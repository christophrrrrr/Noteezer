"""Checks on the static UI in ui/ that need no browser (ADR-8)."""

import json
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[3] / "ui"


def test_the_manifest_makes_the_app_installable_and_a_share_target() -> None:
    manifest = json.loads((UI / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["share_target"]["action"] == "/share"
    assert manifest["share_target"]["enctype"] == "multipart/form-data"
    assert {"192x192", "512x512"} <= {icon["sizes"] for icon in manifest["icons"]}
    for icon in manifest["icons"]:
        assert (UI / icon["src"].lstrip("/")).read_bytes().startswith(b"\x89PNG")


def test_every_file_the_page_and_the_worker_load_exists() -> None:
    page = (UI / "index.html").read_text(encoding="utf-8")
    worker = (UI / "sw.js").read_text(encoding="utf-8")
    paths = re.findall(r'(?:href|src)="(/[^"]+)"', page)
    paths += json.loads(re.search(r"const SHELL = (\[.*?\]);", worker).group(1))
    for path in paths:
        if path != "/":
            assert (UI / path.lstrip("/")).is_file(), path


def test_the_page_runs_no_inline_script_so_a_strict_csp_holds() -> None:
    page = (UI / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)", page) == []
    assert not re.search(r"\son[a-z]+=", page)  # no onclick="..." attributes


def test_note_text_never_becomes_html() -> None:
    # The only markup the UI sets from a string is the fixed icon shapes.
    script = (UI / "app.js").read_text(encoding="utf-8")
    assert len(re.findall(r"\.innerHTML\s*=", script)) == 1
    assert "ICONS[name]" in script.split(".innerHTML =")[1].split("\n")[0]
    for risky in ("outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert risky not in script
