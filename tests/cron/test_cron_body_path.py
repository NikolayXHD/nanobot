from __future__ import annotations

from pathlib import Path

from nanobot.bus.events import (
    PREPARED_BODY_PATH_META,
    InboundMessage,
    OutboundMessage,
)
from nanobot.cron.bound_runner import run_bound_cron_job
from nanobot.cron.types import CronJob, CronPayload, CronSchedule


async def test_file_job_declares_body_path_and_format(tmp_path: Path) -> None:
    agent = _Agent(tmp_path, body="prepared body text")

    await run_bound_cron_job(_job(), agent=agent, cron=_Recorder())

    metadata = agent.seen[0].metadata
    body_path = Path(metadata[PREPARED_BODY_PATH_META])
    assert body_path.parent == tmp_path / "outbox"
    assert body_path.name.endswith(".md")
    assert metadata["body_format"] == "html"
    assert metadata["subject"] == "test subject"


async def test_md_to_html_job_declares_the_format(tmp_path: Path) -> None:
    agent = _Agent(tmp_path, body="prepared body text")

    await run_bound_cron_job(_job(body_format="md_to_html"), agent=agent, cron=_Recorder())

    assert agent.seen[0].metadata["body_format"] == "md_to_html"


async def test_plaintext_job_leaves_the_format_unset(tmp_path: Path) -> None:
    agent = _Agent(tmp_path, body="prepared body text")

    await run_bound_cron_job(_job(body_format="plaintext"), agent=agent, cron=_Recorder())

    assert "body_format" not in agent.seen[0].metadata


async def test_valid_body_is_recorded_as_ok(tmp_path: Path) -> None:
    agent = _Agent(tmp_path, body="prepared body text")
    recorder = _Recorder()

    await run_bound_cron_job(_job(), agent=agent, cron=recorder)

    assert recorder.last is not None
    assert recorder.last["status"] == "ok"
    assert recorder.last["response"] == "prepared body text"


async def test_reply_job_drops_contract_keys_from_origin_metadata(tmp_path: Path) -> None:
    agent = _Agent(tmp_path)
    job = _job(delivery="reply")
    job.payload.origin_metadata.update(
        {
            "prepared_body_path": str(tmp_path / "outbox" / "sneaky.md"),
            "body_format": "html",
        }
    )

    await run_bound_cron_job(job, agent=agent, cron=_Recorder())

    metadata = agent.seen[0].metadata
    assert PREPARED_BODY_PATH_META not in metadata
    assert "body_format" not in metadata


class _Agent:
    def __init__(self, workspace: Path, body: str | None = None):
        self.workspace = workspace
        self.body = body
        self.seen: list[InboundMessage] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        self.seen.append(msg)
        if self.body is not None:
            path = Path(msg.metadata[PREPARED_BODY_PATH_META])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.body, encoding="utf-8")
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=self.body or ""
        )


class _Recorder:
    def __init__(self):
        self.last: dict | None = None

    def write_run_record(self, run_id: str, record: dict) -> None:
        self.last = record


def _job(delivery: str = "file", body_format: str = "html") -> CronJob:
    return CronJob(
        id="job-1",
        name="prepared body job",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="UTC"),
        payload=CronPayload(
            message="run the task",
            delivery=delivery,
            body_format=body_format,
            session_key="email:bot@example.com",
            origin_channel="email",
            origin_chat_id="bot@example.com",
            origin_metadata={"subject": "test subject"},
        ),
    )
