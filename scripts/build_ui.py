"""Builds the UI for one deployment: a copy of ui/ that knows where the services are.

The files in ui/ need no build step. Two things depend on where the services
run, so they are written here:

- config.js, which tells app.js the two addresses;
- a Content-Security-Policy meta tag in index.html that lets the page call
  only those two addresses. Locally nginx sends it as a header instead, but a
  Storage static website cannot set headers.

The CD workflow runs it and uploads the result to the $web container:

    python scripts/build_ui.py --inbox https://... --reminders https://... --out build/ui
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The same policy as docker/ui/default.conf.template, without frame-ancestors,
# which browsers ignore in a meta tag.
POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "connect-src 'self' {inbox} {reminders}; worker-src 'self'; manifest-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'"
)
CHARSET = '<meta charset="utf-8">'


def build(inbox: str, reminders: str, out: Path) -> None:
    inbox, reminders = inbox.rstrip("/"), reminders.rstrip("/")
    for url in (inbox, reminders):
        if not url.startswith(("https://", "http://localhost")) or any(c in url for c in " ;'\""):
            raise SystemExit(f"Not a service address: {url!r}")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "ui", out)

    config = {"inboxUrl": inbox, "remindersUrl": reminders}
    (out / "config.js").write_text(
        "// Written by scripts/build_ui.py for this deployment.\n"
        f"window.NOTEEZER = {json.dumps(config, indent=2)};\n",
        encoding="utf-8",
    )

    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    if html.count(CHARSET) != 1:
        raise SystemExit("index.html must have exactly one charset meta tag")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{POLICY.format(inbox=inbox, reminders=reminders)}">'
    index.write_text(html.replace(CHARSET, CHARSET + "\n  " + meta), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inbox", required=True, help="Base URL of the inbox service")
    parser.add_argument("--reminders", required=True, help="Base URL of the reminders service")
    parser.add_argument("--out", required=True, type=Path, help="Folder to write the UI to")
    args = parser.parse_args()
    build(args.inbox, args.reminders, args.out)
    print(f"UI written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
