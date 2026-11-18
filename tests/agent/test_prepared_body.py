from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.prepared_body import PreparedBodyError, load_prepared_body
from nanobot.bus.events import PREPARED_BODY_PATH_META


def test_when_no_body_is_declared_then_none_is_returned() -> None:
    assert load_prepared_body({}) is None


def test_when_declared_body_is_valid_then_text_is_returned(tmp_path: Path) -> None:
    declared_body_path = tmp_path / "run-1.md"
    declared_body_path.write_text("prepared body text", encoding="utf-8")

    assert load_prepared_body(_declared(declared_body_path)) == "prepared body text"


@pytest.mark.parametrize(
    ("body_bytes", "expected_error"),
    [
        pytest.param(None, "missing", id="absent-file"),
        pytest.param(b"   \n", "no text content", id="blank-file"),
        pytest.param(b"\xff\xfe\x00\x01", "UTF-8", id="non-utf8-file"),
    ],
)
def test_when_declared_body_is_unusable_then_error_is_raised(
    tmp_path: Path, body_bytes: bytes | None, expected_error: str
) -> None:
    declared_body_path = tmp_path / "body.md"
    if body_bytes is not None:
        declared_body_path.write_bytes(body_bytes)

    with pytest.raises(PreparedBodyError, match=expected_error):
        load_prepared_body(_declared(declared_body_path))


def test_when_declared_body_is_a_directory_then_unreadable_error_is_raised(tmp_path: Path) -> None:
    declared_body_path = tmp_path / "body-dir"
    declared_body_path.mkdir()

    with pytest.raises(PreparedBodyError, match="unreadable"):
        load_prepared_body(_declared(declared_body_path))


def test_when_declared_body_exceeds_limit_then_error_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nanobot.agent import prepared_body

    monkeypatch.setattr(prepared_body, "PREPARED_BODY_MAX_BYTES", 8)
    declared_body_path = tmp_path / "big.md"
    declared_body_path.write_text("0123456789", encoding="utf-8")

    with pytest.raises(PreparedBodyError, match="too large"):
        load_prepared_body(_declared(declared_body_path))


def _declared(declared_body_path: Path) -> dict[str, str]:
    return {PREPARED_BODY_PATH_META: str(declared_body_path)}
