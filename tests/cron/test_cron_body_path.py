from __future__ import annotations

import json
from pathlib import Path

from nanobot.bus.events import PREPARED_BODY_PATH_META, InboundMessage, OutboundMessage
from nanobot.cron.bound_runner import run_bound_cron_job
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronPayload, CronSchedule


async def test_when_delivery_is_file_then_body_path_is_declared(tmp_path: Path) -> None:
    agent = _RecordingAgent(tmp_path)

    await run_bound_cron_job(_job(delivery="file"), agent=agent, cron=_RunRecorder())

    declared_body_path = Path(agent.seen[0].metadata[PREPARED_BODY_PATH_META])
    assert declared_body_path.parent == tmp_path / "outbox"
    assert declared_body_path.name.endswith(".md")


async def test_when_delivery_is_reply_then_no_body_path_is_declared(tmp_path: Path) -> None:
    agent = _RecordingAgent(tmp_path)

    await run_bound_cron_job(_job(delivery="reply"), agent=agent, cron=_RunRecorder())

    assert PREPARED_BODY_PATH_META not in agent.seen[0].metadata


async def test_when_origin_metadata_declares_body_path_then_it_is_dropped(tmp_path: Path) -> None:
    agent = _RecordingAgent(tmp_path)
    job = _job(delivery="reply")
    job.payload.origin_metadata.update(
        {PREPARED_BODY_PATH_META: str(tmp_path / "outbox" / "sneaky.md")}
    )

    await run_bound_cron_job(job, agent=agent, cron=_RunRecorder())

    assert PREPARED_BODY_PATH_META not in agent.seen[0].metadata


def test_when_jobs_file_is_rewritten_then_delivery_field_survives(tmp_path: Path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps(_jobs_file_with_delivery()), encoding="utf-8")

    service = CronService(store_path)
    store = service._load_store()
    assert store is not None
    service._write_store(store)

    payload = json.loads(store_path.read_text(encoding="utf-8"))["jobs"][0]["payload"]
    assert payload["delivery"] == "file"


class _RecordingAgent:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.seen: list[InboundMessage] = []

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        self.seen.append(msg)
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")


class _RunRecorder:
    def write_run_record(self, run_id: str, record: dict) -> None:
        pass


def _job(delivery: str) -> CronJob:
    return CronJob(
        id="job-1",
        name="prepared body job",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="UTC"),
        payload=CronPayload(
            message="run the task",
            delivery=delivery,
            session_key="email:bot@example.com",
            origin_channel="email",
            origin_chat_id="bot@example.com",
        ),
    )


def _jobs_file_with_delivery() -> dict:
    return {
        "version": 1,
        "jobs": [
            {
                "id": "job-1",
                "name": "prepared body job",
                "enabled": True,
                "schedule": {
                    "kind": "cron",
                    "atMs": None,
                    "everyMs": None,
                    "expr": "0 9 * * *",
                    "tz": "UTC",
                },
                "payload": {
                    "kind": "agent_turn",
                    "message": "run the task",
                    "delivery": "file",
                    "deliver": False,
                    "channel": None,
                    "to": None,
                    "channelMeta": {},
                    "sessionKey": "email:bot@example.com",
                    "originChannel": "email",
                    "originChatId": "bot@example.com",
                    "originMetadata": {},
                },
            }
        ],
    }
