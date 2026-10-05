from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.prepared_body import PreparedBodyError, load_prepared_body
from nanobot.bus.events import PREPARED_BODY_PATH_META


def test_undeclared_body_returns_none(tmp_path: Path) -> None:
    assert load_prepared_body({}) is None


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PreparedBodyError, match="missing"):
        load_prepared_body(_declared(tmp_path / "missing.md"))


def test_empty_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("   \n", encoding="utf-8")

    with pytest.raises(PreparedBodyError, match="no text content"):
        load_prepared_body(_declared(path))


def test_oversized_file_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nanobot.agent import prepared_body

    monkeypatch.setattr(prepared_body, "PREPARED_BODY_MAX_BYTES", 8)
    path = tmp_path / "big.md"
    path.write_text("0123456789", encoding="utf-8")

    with pytest.raises(PreparedBodyError, match="too large"):
        load_prepared_body(_declared(path))


def test_non_utf8_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "binary.md"
    path.write_bytes(b"\xff\xfe\x00\x01")

    with pytest.raises(PreparedBodyError, match="UTF-8"):
        load_prepared_body(_declared(path))


def test_valid_file_returns_text(tmp_path: Path) -> None:
    path = tmp_path / "run-1.md"
    path.write_text("prepared body text", encoding="utf-8")

    assert load_prepared_body(_declared(path)) == (
        "prepared body text"
    )


def test_directory_is_reported_as_unreadable(tmp_path: Path) -> None:
    directory = tmp_path / "body-dir"
    directory.mkdir()

    with pytest.raises(PreparedBodyError, match="unreadable"):
        load_prepared_body(_declared(directory))


def _declared(path: Path) -> dict[str, str]:
    return {PREPARED_BODY_PATH_META: str(path)}
