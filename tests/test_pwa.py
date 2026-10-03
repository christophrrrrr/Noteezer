import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noteezer.main import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as test_client:
        yield test_client


def _bodies(client: TestClient) -> list[str]:
    from noteezer.db import connect

    connection = connect()
    rows = connection.execute("SELECT body FROM items ORDER BY id").fetchall()
    connection.close()
    return [row["body"] for row in rows]


def test_the_manifest_makes_the_app_installable_and_a_share_target(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(response.text)
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/inbox"
    assert manifest["share_target"]["action"] == "/share"
    assert manifest["share_target"]["enctype"] == "multipart/form-data"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        image = client.get(icon["src"])
        assert image.status_code == 200
        assert image.content.startswith(b"\x89PNG")


def test_the_service_worker_is_served_from_the_root(client: TestClient) -> None:
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-cache"
    assert client.get("/static/offline.html").status_code == 200
    page = client.get("/login")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page.text


def test_sharing_text_and_links_saves_each_piece_once(client: TestClient) -> None:
    assert client.post("/share", data={"text": "hello"}, follow_redirects=False).headers["location"] == "/login"
    client.post("/register", data={"email": "a@example.com", "password": "password1"})

    youtube = client.post(
        "/share",
        data={"title": "", "text": "https://youtu.be/mG-xk4SLI80", "url": ""},
        follow_redirects=False,
    )
    assert youtube.status_code == 303
    assert youtube.headers["location"] == "/inbox?folder=unsorted"
    client.post(
        "/share",
        data={"title": "Bloque 21", "text": "Bloque 21", "url": "https://bloque21.example/comp"},
    )
    client.post("/share", data={"title": "", "text": "", "url": ""})
    assert _bodies(client) == ["https://youtu.be/mG-xk4SLI80", "Bloque 21\nhttps://bloque21.example/comp"]


def test_sharing_a_screenshot_saves_the_image(client: TestClient) -> None:
    client.post("/register", data={"email": "a@example.com", "password": "password1"})
    shared = client.post("/share", files={"image": ("shot.png", PNG, "image/png")}, follow_redirects=False)
    assert shared.status_code == 303
    assert _bodies(client) == ["Screenshot"]
    assert client.get("/items/1/image").content == PNG

    refused = client.post("/share", files={"image": ("x.png", b"<svg/>", "image/png")})
    assert refused.status_code == 400
    assert len(_bodies(client)) == 1


def test_the_login_lasts_so_the_phone_app_stays_signed_in(client: TestClient) -> None:
    response = client.post(
        "/register", data={"email": "a@example.com", "password": "password1"}, follow_redirects=False
    )
    cookie = response.headers["set-cookie"].lower()
    assert "max-age=7776000" in cookie
    assert "httponly" in cookie
