import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noteezer.db import connect, init_db
from noteezer.folders import create_folder
from noteezer.gemini import GeminiSorter, build_prompt, parse_answer
from noteezer.images import (
    MAX_BYTES,
    PLACEHOLDER,
    ImageError,
    attach_image,
    check_image,
    image_for,
    images_for,
    readable_image,
)
from noteezer.inbox import list_items, save_item
from noteezer.main import create_app
from noteezer.sorting import FolderOption, Note, Part, Suggestion, results_for, sort_pending
from noteezer.users import register_user

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection, email: str = "a@example.com") -> int:
    return register_user(connection, email, "password1", NOW)


class Reader:
    """A fake engine that reads every image the same way and records what it saw."""

    name = "reader"

    def __init__(self, suggestion: Suggestion) -> None:
        self.suggestion = suggestion
        self.notes: list[Note] = []

    def suggest(self, note, folders):
        self.notes.append(note)
        return self.suggestion


def test_only_known_image_formats_are_accepted() -> None:
    assert check_image(PNG) == "image/png"
    assert check_image(JPEG) == "image/jpeg"
    assert check_image(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert check_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    for bad in (b"", b"<svg onload=alert(1)>", b"%PDF-1.7", PNG[:4]):
        with pytest.raises(ImageError):
            check_image(bad)
    with pytest.raises(ImageError):
        check_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_BYTES)


def test_an_image_belongs_to_its_owner_and_its_privacy_choice_is_kept(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    other = _user(connection, "b@example.com")
    shared = save_item(connection, owner, PLACEHOLDER, None, NOW)
    private = save_item(connection, owner, "bank screenshot", None, NOW)
    attach_image(connection, shared, PNG, ai_allowed=True)
    attach_image(connection, private, JPEG, ai_allowed=False)

    assert image_for(connection, owner, shared).mime == "image/png"
    assert image_for(connection, other, shared) is None
    assert readable_image(connection, shared).data == PNG
    assert readable_image(connection, private) is None
    assert images_for(connection, owner)[private]["ai_allowed"] is False
    connection.close()


def test_the_reading_replaces_the_placeholder_but_never_typed_text(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    climbing = create_folder(connection, owner, "Climbing", NOW)
    bare = save_item(connection, owner, PLACEHOLDER, None, NOW)
    typed = save_item(connection, owner, "gym hours", None, NOW)
    attach_image(connection, bare, PNG, ai_allowed=True)
    attach_image(connection, typed, PNG, ai_allowed=True)
    reader = Reader(
        Suggestion(
            climbing,
            0.9,
            image_title="Climbing gym opening hours",
            image_text="Mon-Fri 10-22, Sat 9-20",
            parts=(Part("a", None, None, 0), Part("b", None, None, 0)),
        )
    )
    assert sort_pending(connection, reader, NOW) == 2
    assert all(note.image is not None for note in reader.notes)

    listed = {item["id"]: item for item in list_items(connection, owner, folder_id=climbing)}
    assert listed[bare]["body"] == "Climbing gym opening hours"
    assert listed[typed]["body"] == "gym hours"
    assert images_for(connection, owner)[typed]["text"] == "Mon-Fri 10-22, Sat 9-20"
    assert {item["id"] for item in list_items(connection, owner, query="sat 9-20")} == {bare, typed}
    assert len(list_items(connection, owner)) == 2, "a screenshot is never split"
    connection.close()


def test_a_private_image_is_never_sent_and_a_filed_one_is_still_read(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    climbing = create_folder(connection, owner, "Climbing", NOW)
    private = save_item(connection, owner, "bank app", None, NOW)
    attach_image(connection, private, PNG, ai_allowed=False)
    filed = save_item(connection, owner, PLACEHOLDER, None, NOW, folder_id=climbing)
    attach_image(connection, filed, PNG, ai_allowed=True)
    private_filed = save_item(connection, owner, PLACEHOLDER, None, NOW, folder_id=climbing)
    attach_image(connection, private_filed, PNG, ai_allowed=False)

    reader = Reader(Suggestion(None, 0.0, image_title="Route topo for Albarracin"))
    assert sort_pending(connection, reader, NOW) == 2
    seen = {note.body: note.image for note in reader.notes}
    assert seen["bank app"] is None
    assert seen[PLACEHOLDER] is not None
    assert list_items(connection, owner, folder_id=climbing)[0]["folder_ids"] == [climbing]
    assert {item["body"] for item in list_items(connection, owner, folder_id=climbing)} == {
        "Route topo for Albarracin",
        PLACEHOLDER,
    }
    assert private_filed not in results_for(connection, owner)
    connection.close()


def test_gemini_gets_the_image_and_returns_a_title_and_its_text() -> None:
    folders = [FolderOption(1, "Climbing", "Climbing")]
    assert "Image: attached" not in build_prompt(Note(PLACEHOLDER), folders)

    from noteezer.images import Image

    picture = Image(PNG, "image/png")
    seen = []

    def model(system, prompt, image=None, schema=None):
        seen.append((prompt, image))
        return json.dumps(
            {
                "folder_id": 1,
                "confidence": 0.9,
                "new_folder": None,
                "kind": "note",
                "remind_at": None,
                "parts": [],
                "image_title": "  Gym hours  ",
                "image_text": "x" * 2000,
            }
        )

    suggestion = GeminiSorter(model, min_interval=0).suggest(Note(PLACEHOLDER, image=picture), folders)
    assert "Image: attached" in seen[0][0]
    assert seen[0][1] is picture
    assert suggestion.image_title == "Gym hours"
    assert len(suggestion.image_text) == 1000
    blank = parse_answer(json.dumps({"image_title": " ", "image_text": 5}), folders)
    assert (blank.image_title, blank.image_text) == (None, None)


def test_uploading_and_viewing_an_image_through_the_pages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as client:
        client.post("/register", data={"email": "a@example.com", "password": "password1"})
        saved = client.post(
            "/inbox",
            data={"body": "", "ai_image": "1"},
            files={"image": ("shot.png", PNG, "image/png")},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        page = client.get("/inbox")
        assert '<img class="shot" src="/items/1/image"' in page.text
        assert ">Screenshot</div>" in page.text

        picture = client.get("/items/1/image")
        assert picture.content == PNG
        assert picture.headers["content-type"] == "image/png"
        assert picture.headers["x-content-type-options"] == "nosniff"

        refused = client.post("/inbox", data={"body": ""}, files={"image": ("x.svg", b"<svg/>", "image/png")})
        assert refused.status_code == 400
        assert "Use a PNG, JPEG, WebP, or GIF image." in refused.text
        # What a browser sends when no file was chosen: a file part with an empty name.
        boundary = "----NoFileChosen"
        no_file = "\r\n".join(
            [
                f"--{boundary}",
                'Content-Disposition: form-data; name="body"',
                "",
                "",
                f"--{boundary}",
                'Content-Disposition: form-data; name="image"; filename=""',
                "Content-Type: application/octet-stream",
                "",
                "",
                f"--{boundary}--",
                "",
            ]
        ).encode()
        empty = client.post(
            "/inbox", content=no_file, headers={"content-type": f"multipart/form-data; boundary={boundary}"}
        )
        assert empty.status_code == 400
        assert "Write something before saving." in empty.text

        client.post("/logout")
        client.post("/register", data={"email": "b@example.com", "password": "password1"})
        assert client.get("/items/1/image").status_code == 404
