import ipaddress
import json
import socket
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from noteezer.classify import URL_PATTERN

MAX_BYTES = 512_000
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 5.0
USER_AGENT = "Mozilla/5.0 (compatible; Noteezer/1.0; +https://github.com/christophrrrrr/Noteezer)"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

# Trust the operating system's certificates, like a browser does. Python's own
# list misses roots added by the OS or by antivirus HTTPS scanning.
TLS_CONTEXT = ssl.create_default_context()

# Tests swap this for httpx.MockTransport so no request leaves the machine.
_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class LinkPreview:
    url: str
    title: str
    description: str
    site: str


def first_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:!?)]}'\"")


def fetch_preview(url: str) -> LinkPreview | None:
    """Title and description of a link, or None when it cannot be read.

    YouTube is read through its oEmbed endpoint because the video page itself
    is mostly script. Every other link is read from the page's <head>.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
        if host in YOUTUBE_HOSTS:
            return _youtube(url)
        return _page(url, host)
    except (httpx.HTTPError, ValueError, UnicodeError):
        return None


def save_preview(
    connection: sqlite3.Connection,
    item_id: int,
    url: str,
    preview: LinkPreview | None,
    now: datetime,
) -> None:
    # A failed fetch is stored with an empty title so it is not fetched again.
    connection.execute(
        """
        INSERT INTO link_previews (item_id, url, title, description, site, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            url = excluded.url,
            title = excluded.title,
            description = excluded.description,
            site = excluded.site,
            fetched_at = excluded.fetched_at
        """,
        (
            item_id,
            url,
            preview.title if preview else "",
            preview.description if preview else "",
            preview.site if preview else "",
            now.astimezone(timezone.utc).isoformat(),
        ),
    )
    connection.commit()


def stored_preview(connection: sqlite3.Connection, item_id: int) -> tuple[str, LinkPreview | None] | None:
    row = connection.execute(
        "SELECT url, title, description, site FROM link_previews WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    if not row["title"]:
        return row["url"], None
    return row["url"], LinkPreview(row["url"], row["title"], row["description"], row["site"])


def previews_for(connection: sqlite3.Connection, user_id: int) -> dict[int, dict]:
    rows = connection.execute(
        """
        SELECT p.item_id, p.url, p.title, p.description, p.site
        FROM link_previews p
        JOIN items i ON i.id = p.item_id
        WHERE i.user_id = ? AND p.title != ''
        """,
        (user_id,),
    ).fetchall()
    return {
        int(row["item_id"]): {
            "url": row["url"],
            "title": row["title"],
            "description": row["description"],
            "site": row["site"],
        }
        for row in rows
    }


def _youtube(url: str) -> LinkPreview | None:
    body = _download("https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"}))
    if body is None:
        return None
    data = json.loads(body)
    title = _squash(str(data.get("title", "")))
    author = _squash(str(data.get("author_name", "")))
    if not title:
        return None
    description = f"YouTube video by {author}" if author else "YouTube video"
    return LinkPreview(url, title[:300], description, "youtube.com")


def _page(url: str, host: str) -> LinkPreview | None:
    body = _download(url, html_only=True)
    if body is None:
        return None
    parser = _HeadParser()
    parser.feed(body)
    title = _squash(parser.og_title or parser.title)
    if not title:
        return None
    site = host.removeprefix("www.")
    return LinkPreview(url, title[:300], _squash(parser.description)[:500], site)


def _download(url: str, html_only: bool = False) -> str | None:
    # Redirects are followed by hand so every hop is checked by _is_public.
    # That keeps a saved link from making the server read its own network,
    # like a cloud metadata address.
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en,es;q=0.8"}
    with httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers=headers,
        verify=TLS_CONTEXT,
        transport=_transport,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname or not _is_public(parts.hostname):
                return None
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    return None
                if html_only and "html" not in response.headers.get("content-type", ""):
                    return None
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) >= MAX_BYTES:
                        break
                return bytes(data[:MAX_BYTES]).decode(response.charset_encoding or "utf-8", errors="replace")
    return None


def _is_public(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for address in addresses:
        ip = ipaddress.ip_address(str(address[4][0]).split("%")[0])
        if not ip.is_global:
            return False
    return bool(addresses)


def _squash(text: str) -> str:
    return " ".join(text.split())


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.og_title = ""
        self.description = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            values = dict(attrs)
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content") or ""
            if key == "og:title" and not self.og_title:
                self.og_title = content
            elif key in ("og:description", "description") and not self.description:
                self.description = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
