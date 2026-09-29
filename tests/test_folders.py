from datetime import datetime, timezone
from pathlib import Path

import pytest

from noteezer.db import connect, init_db
from noteezer.folders import (
    FolderError,
    create_folder,
    delete_folder,
    descendant_ids,
    list_folders,
    move_folder,
    rename_folder,
)
from noteezer.inbox import count_by_folder, list_items, move_item, save_item
from noteezer.users import register_user

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection, email: str = "a@example.com") -> int:
    return register_user(connection, email, "password1", NOW)


def test_tree_is_listed_depth_first_with_paths(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    projects = create_folder(connection, owner, "Projects", NOW)
    game = create_folder(connection, owner, "Game", NOW, parent_id=projects)
    create_folder(connection, owner, "Cannon Circle", NOW, parent_id=game)
    create_folder(connection, owner, "Climbing", NOW, parent_id=projects)
    create_folder(connection, owner, "Movies", NOW)

    listed = [(entry["depth"], entry["path"]) for entry in list_folders(connection, owner)]
    assert listed == [
        (0, "Movies"),
        (0, "Projects"),
        (1, "Projects / Climbing"),
        (1, "Projects / Game"),
        (2, "Projects / Game / Cannon Circle"),
    ]
    connection.close()


def test_sibling_names_are_unique_but_cousins_can_share_a_name(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    noteezer = create_folder(connection, owner, "Noteezer", NOW)
    create_folder(connection, owner, "Ideas", NOW, parent_id=game)
    create_folder(connection, owner, "Ideas", NOW, parent_id=noteezer)
    with pytest.raises(FolderError):
        create_folder(connection, owner, "ideas", NOW, parent_id=game)
    with pytest.raises(FolderError):
        create_folder(connection, owner, "game", NOW)
    with pytest.raises(FolderError):
        rename_folder(connection, owner, noteezer, "GAME")
    connection.close()


def test_names_are_cleaned_and_checked(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    folder_id = create_folder(connection, owner, "  Watch   later ", NOW)
    assert list_folders(connection, owner)[0]["name"] == "Watch later"
    for bad in ("   ", "a/b", "x" * 61):
        with pytest.raises(FolderError):
            create_folder(connection, owner, bad, NOW)
    rename_folder(connection, owner, folder_id, "Movies")
    assert list_folders(connection, owner)[0]["name"] == "Movies"
    connection.close()


def test_another_users_folder_cannot_be_used(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    other = _user(connection, "b@example.com")
    theirs = create_folder(connection, other, "Private", NOW)
    with pytest.raises(LookupError):
        create_folder(connection, owner, "Inside", NOW, parent_id=theirs)
    with pytest.raises(LookupError):
        save_item(connection, owner, "hello", None, NOW, folder_id=theirs)
    with pytest.raises(LookupError):
        delete_folder(connection, owner, theirs)
    assert list_folders(connection, owner) == []
    connection.close()


def test_a_folder_cannot_move_inside_itself(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    projects = create_folder(connection, owner, "Projects", NOW)
    game = create_folder(connection, owner, "Game", NOW, parent_id=projects)
    maps = create_folder(connection, owner, "Maps", NOW, parent_id=game)
    assert descendant_ids(connection, owner, projects) == {projects, game, maps}
    with pytest.raises(FolderError):
        move_folder(connection, owner, projects, maps)
    with pytest.raises(FolderError):
        move_folder(connection, owner, game, game)

    move_folder(connection, owner, maps, None)
    paths = [entry["path"] for entry in list_folders(connection, owner)]
    assert paths == ["Maps", "Projects", "Projects / Game"]
    connection.close()


def test_deleting_a_folder_keeps_its_items_in_unsorted(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    projects = create_folder(connection, owner, "Projects", NOW)
    game = create_folder(connection, owner, "Game", NOW, parent_id=projects)
    keep = create_folder(connection, owner, "Movies", NOW)
    save_item(connection, owner, "idea king of the hill", None, NOW, folder_id=game)
    save_item(connection, owner, "fix cannon hitbox", None, NOW, folder_id=projects)
    save_item(connection, owner, "Dune part 3", None, NOW, folder_id=keep)

    delete_folder(connection, owner, projects)
    assert [entry["name"] for entry in list_folders(connection, owner)] == ["Movies"]
    assert count_by_folder(connection, owner) == {None: 2, keep: 1}
    unsorted = list_items(connection, owner, unsorted=True)
    assert sorted(item["body"] for item in unsorted) == ["fix cannon hitbox", "idea king of the hill"]
    connection.close()


def test_items_are_filtered_and_moved_between_folders(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    movies = create_folder(connection, owner, "Movies", NOW)
    dune = save_item(connection, owner, "Dune part 3", None, NOW)
    save_item(connection, owner, "Interstellar", None, NOW, folder_id=movies)

    assert [item["body"] for item in list_items(connection, owner, folder_id=movies)] == ["Interstellar"]
    assert [item["body"] for item in list_items(connection, owner, unsorted=True)] == ["Dune part 3"]
    assert len(list_items(connection, owner)) == 2

    move_item(connection, owner, dune, movies)
    assert count_by_folder(connection, owner) == {movies: 2}
    move_item(connection, owner, dune, None)
    assert list_items(connection, owner, unsorted=True)[0]["id"] == dune
    with pytest.raises(LookupError):
        move_item(connection, owner, 999, movies)
    connection.close()
