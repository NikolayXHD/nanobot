"""Git channel implementation using a local Git repository as a communication medium.

Sessions are subdirectories matching `session_dir_pattern` (default "session-*").
Messages are numbered files `nnn-who.md` (e.g., "001-user.md", "002-bot.md").
State is stored in a `.cursor` file containing the last processed message number.

The channel periodically polls the repository, pulls with rebase to stay up-to-date,
reads new messages from sessions, and forwards them to the bus.
Outbound messages are written as numbered files, committed, and pushed (if auto_push).
"""

import asyncio
import re
import os
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


class GitConfig(BaseModel):
    """Git channel configuration."""

    enabled: bool = False
    local_path: str = Field(default="", description="Path to local Git repository clone")
    branch: str = Field(default="main", description="Branch to work on")
    remote: str = Field(default="origin", description="Remote name")
    poll_interval_seconds: int = Field(default=30, ge=5, le=3600,
                                       description="How often to check for new messages")
    session_dir_pattern: str = Field(default="session-*",
                                     description="Glob pattern for session directories")
    message_file_pattern: str = Field(default="*.md",
                                      description="Glob pattern for message files")
    cursor_file: str = Field(default=".cursor", description="Name of the cursor file")
    auto_push: bool = Field(default=True, description="Automatically push after sending a response")
    conflict_resolution: str = Field(default="rebase", description="Conflict resolution strategy: rebase, merge, abort")

    class Config:
        alias_generator = lambda s: s  # keep snake_case
        populate_by_name = True


class GitChannel(BaseChannel):
    """Git channel."""

    name = "git"
    display_name = "Git"

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return GitConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = GitConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: GitConfig = config
        self._running = False
        self._repo_path = Path(self.config.local_path).expanduser().resolve()
        self._session_glob = self.config.session_dir_pattern
        self._message_glob = self.config.message_file_pattern
        self._cursor_file = self.config.cursor_file

    async def start(self) -> None:
        """Start polling the Git repository for new messages."""
        if not self.config.enabled:
            logger.warning("Git channel disabled: enabled is false")
            return

        if not self._repo_path.exists():
            logger.error("Git channel: local path does not exist: {}", self._repo_path)
            return

        if not self._is_git_repo():
            logger.error("Git channel: not a Git repository: {}", self._repo_path)
            return

        logger.info("Starting Git channel for repository {} (branch {})",
                    self._repo_path, self.config.branch)

        self._running = True
        poll_seconds = max(5, int(self.config.poll_interval_seconds))

        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.error("Git polling error: {}", e)
                # If a critical error occurs (e.g., conflict), stop the channel?
                # For now, just log and continue.
            await asyncio.sleep(poll_seconds)

    async def stop(self) -> None:
        """Stop polling loop."""
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message by writing a file in the appropriate session."""
        if not self.config.enabled:
            logger.warning("Git channel disabled, cannot send")
            return

        # Determine session directory from chat_id.
        # chat_id is expected to be the session directory name (relative to repo root).
        session_dir = self._repo_path / msg.chat_id
        if not session_dir.exists():
            logger.error("Session directory does not exist: {}", session_dir)
            return

        # Find the next message number.
        next_num = self._next_message_number(session_dir)
        filename = f"{next_num:03d}-bot.md"
        file_path = session_dir / filename

        # Write the message content.
        try:
            file_path.write_text(msg.content, encoding="utf-8")
            logger.info("Wrote message to {}", file_path)
        except Exception as e:
            logger.error("Failed to write message file: {}", e)
            raise

        # Update cursor: we have just written a message, but cursor marks last read message.
        # For simplicity, we don't update cursor here; cursor is only for incoming messages.

        # Stage, commit, and optionally push.
        await self._git_add(str(file_path))
        await self._git_commit(f"nanobot: response in {msg.chat_id} ({filename})")

        if self.config.auto_push:
            await self._git_push()

    async def _poll(self) -> None:
        """Poll the repository: pull, find sessions, process new messages."""
        # Ensure we are on the correct branch.
        await self._git_checkout_branch()

        # Pull with rebase to get latest changes.
        if not await self._git_pull_rebase():
            logger.error("Git pull --rebase failed; skipping this poll cycle")
            return

        # Find all session directories.
        sessions = self._find_sessions()
        if not sessions:
            return

        # Process each session.
        for session_path in sessions:
            await self._process_session(session_path)

    def _is_git_repo(self) -> bool:
        """Check if the configured path is a Git repository."""
        git_dir = self._repo_path / ".git"
        return git_dir.exists() and git_dir.is_dir()

    async def _git_checkout_branch(self) -> None:
        """Checkout the configured branch."""
        try:
            await self._run_git_command(["checkout", self.config.branch])
        except Exception as e:
            logger.warning("Failed to checkout branch {}: {}", self.config.branch, e)

    async def _git_pull_rebase(self) -> bool:
        """Pull from remote with rebase. Return True on success."""
        try:
            await self._run_git_command(["pull", "--rebase", self.config.remote, self.config.branch])
            return True
        except Exception as e:
            logger.error("Git pull --rebase failed: {}", e)
            return False

    async def _git_add(self, path: str) -> None:
        """Stage a file."""
        try:
            await self._run_git_command(["add", path])
        except Exception as e:
            logger.error("Git add failed: {}", e)
            raise

    async def _git_commit(self, message: str) -> None:
        """Commit staged changes."""
        try:
            await self._run_git_command(["commit", "-m", message])
        except Exception as e:
            logger.error("Git commit failed: {}", e)
            raise

    async def _git_push(self) -> None:
        """Push commits to remote."""
        try:
            await self._run_git_command(["push", self.config.remote, self.config.branch])
        except Exception as e:
            logger.error("Git push failed: {}", e)
            raise

    async def _run_git_command(self, args: List[str]) -> str:
        """Run a Git command and return its stdout."""
        cmd = ["git"] + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self._repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Git command failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    def _find_sessions(self) -> List[Path]:
        """Return list of session directories matching the pattern."""
        sessions = []
        for item in self._repo_path.glob(self._session_glob):
            if item.is_dir():
                sessions.append(item)
        return sessions

    async def _process_session(self, session_path: Path) -> None:
        """Read new messages from a session directory and forward them to the bus."""
        cursor_path = session_path / self._cursor_file
        last_read = 0
        if cursor_path.exists():
            try:
                content = cursor_path.read_text(encoding="utf-8").strip()
                last_read = int(content) if content.isdigit() else 0
            except Exception as e:
                logger.warning("Failed to read cursor file {}: {}", cursor_path, e)

        # Find all message files matching the pattern.
        message_files = []
        for file_path in session_path.glob(self._message_glob):
            match = re.match(r"^(\d+)-(\w+)\.md$", file_path.name)
            if match:
                num = int(match.group(1))
                who = match.group(2)
                message_files.append((num, who, file_path))

        if not message_files:
            return

        # Sort by number.
        message_files.sort(key=lambda x: x[0])

        new_messages = []
        for num, who, file_path in message_files:
            if num > last_read:
                new_messages.append((num, who, file_path))

        if not new_messages:
            return

        # Process new messages in order.
        for num, who, file_path in new_messages:
            try:
                content = file_path.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.error("Failed to read message file {}: {}", file_path, e)
                continue

            # Determine sender_id. For user messages, we need an identifier.
            # Use the session directory name plus the user name (who).
            sender_id = f"{session_path.name}:{who}"
            chat_id = session_path.name  # session directory name

            # Forward to the bus.
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                media=None,
                metadata={"file": str(file_path), "number": num, "author": who},
            )

            # Update cursor after successful processing.
            last_read = num
            try:
                cursor_path.write_text(str(last_read), encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to update cursor file {}: {}", cursor_path, e)

    def _next_message_number(self, session_path: Path) -> int:
        """Find the next available message number in the session."""
        max_num = 0
        for file_path in session_path.glob(self._message_glob):
            match = re.match(r"^(\d+)-(\w+)\.md$", file_path.name)
            if match:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
        return max_num + 1