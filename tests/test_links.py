from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from noteezer import links
from noteezer.db import connect, init_db
from noteezer.folders import create_folder
from noteezer.inbox import save_item
from noteezer.links import LinkPreview, fetch_preview, first_url, previews_for, stored_preview
from noteezer.sorting import RulesSorter, results_for, sort_pending
from noteezer.users import register_user

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

PAGE = """<html><head>
<title>Fallback title</title>
<meta property="og:title" content="Rock climbing &amp; computer vision">
<meta name="description" content="  A tool that   tracks holds. ">
</head><body>ignored</body></html>"""


@pytest.fixture
def web(monkeypatch):
    """Serves fake pages and treats every host except internal.test as public."""
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.url.host}{request.url.path}"
        return routes.get(key, httpx.Response(404))

    monkeypatch.setattr(links, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(links, "_is_public", lambda host: host != "internal.test")
    return routes


def test_first_url_drops_trailing_punctuation() -> None:
    assert first_url("see https://example.com/a?b=1). thanks") == "https://example.com/a?b=1"
    assert first_url("no link here") is None


@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "192.168.0.10", "169.254.169.254", "::1"])
def test_private_and_local_addresses_are_not_public(host: str) -> None:
    assert links._is_public(host) is False


def test_a_public_address_is_public() -> None:
    assert links._is_public("8.8.8.8") is True


def test_page_title_and_description_come_from_the_head(web) -> None:
    web["www.example.com/post"] = httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})
    preview = fetch_preview("https://www.example.com/post")
    assert preview == LinkPreview(
        "https://www.example.com/post",
        "Rock climbing & computer vision",
        "A tool that tracks holds.",
        "example.com",
    )


def test_youtube_is_read_through_oembed(web) -> None:
    web["www.youtube.com/oembed"] = httpx.Response(
        200, json={"title": "5 tips for crimps", "author_name": "Climb Coach"}
    )
    preview = fetch_preview("https://youtu.be/mG-xk4SLI80")
    assert preview is not None
    assert preview.title == "5 tips for crimps"
    assert preview.description == "YouTube video by Climb Coach"
    assert preview.site == "youtube.com"


def test_redirects_are_followed_but_never_into_the_local_network(web) -> None:
    web["short.test/a"] = httpx.Response(302, headers={"location": "https://www.example.com/post"})
    web["www.example.com/post"] = httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
    web["short.test/b"] = httpx.Response(302, headers={"location": "http://internal.test/secrets"})
    web["internal.test/secrets"] = httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    assert fetch_preview("https://short.test/a").title == "Rock climbing & computer vision"
    assert fetch_preview("https://short.test/b") is None
    assert fetch_preview("http://internal.test/secrets") is None


def test_unreadable_links_give_no_preview(web, monkeypatch) -> None:
    web["www.example.com/file.pdf"] = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    web["www.example.com/empty"] = httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
    assert fetch_preview("https://www.example.com/missing") is None
    assert fetch_preview("https://www.example.com/file.pdf") is None
    assert fetch_preview("https://www.example.com/empty") is None
    assert fetch_preview("ftp://example.com/file") is None

    def refuse(request):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(links, "_transport", httpx.MockTransport(refuse))
    assert fetch_preview("https://www.example.com/post") is None


def test_sorting_fetches_a_link_once_and_matches_its_title(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    owner = register_user(connection, "a@example.com", "password1", NOW)
    climbing = create_folder(connection, owner, "Climbing", NOW)
    video = save_item(connection, owner, "https://youtu.be/abc", None, NOW)
    dead = save_item(connection, owner, "https://www.linkedin.com/posts/xyz", None, NOW)
    calls: list[str] = []

    def fake_fetch(url: str) -> LinkPreview | None:
        calls.append(url)
        if "youtu" in url:
            return LinkPreview(url, "Climbing footwork drills", "YouTube video by Coach", "youtube.com")
        return None

    sort_pending(connection, RulesSorter(), NOW, fetch_preview=fake_fetch)
    assert results_for(connection, owner)[video]["status"] == "filed"
    assert results_for(connection, owner)[video]["folder_id"] == climbing
    assert stored_preview(connection, dead) == ("https://www.linkedin.com/posts/xyz", None)
    assert list(previews_for(connection, owner)) == [video]

    connection.execute("DELETE FROM sort_results")
    sort_pending(connection, RulesSorter(), NOW, fetch_preview=fake_fetch)
    assert len(calls) == 2
    connection.close()
