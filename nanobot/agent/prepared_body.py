"""Ready-made message bodies that bypass direct LLM output.

A turn may declare that its user-visible message is a file prepared by a
deterministic tool (for example, a report renderer). The body replaces the agent
reply before the turn is delivered, while the session transcript keeps the
model's own report. When the declared file is missing or unreadable, the error
text is delivered instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nanobot.bus.events import PREPARED_BODY_PATH_META

PREPARED_BODY_MAX_BYTES = 1024 * 1024


class PreparedBodyError(RuntimeError):
    """Raised when a declared body file cannot serve as the turn message."""


def turn_body_path(workspace: Path | str, turn_id: str) -> Path:
    """Return the file that carries the reply body of a turn."""
    return Path(workspace) / "outbox" / f"{turn_id}.md"


def load_prepared_body(metadata: Mapping[str, Any]) -> str | None:
    """Read the declared body file; None when the turn declares none.

    Raises PreparedBodyError when the declared file cannot be delivered.
    """
    declared = metadata.get(PREPARED_BODY_PATH_META)
    if declared is None:
        return None
    path = Path(declared)
    try:
        with path.open("rb") as stream:
            data = stream.read(PREPARED_BODY_MAX_BYTES + 1)
    except FileNotFoundError as exc:
        raise PreparedBodyError(f"prepared body file is missing: {path}") from exc
    except OSError as exc:
        raise PreparedBodyError(f"prepared body file is unreadable: {path} ({exc})") from exc
    if len(data) > PREPARED_BODY_MAX_BYTES:
        raise PreparedBodyError(f"prepared body file is too large: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PreparedBodyError(f"prepared body file is not UTF-8 text: {path}") from exc
    if not text.strip():
        raise PreparedBodyError(f"prepared body file has no text content: {path}")
    return text
