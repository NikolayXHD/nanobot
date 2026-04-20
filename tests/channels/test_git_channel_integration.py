"""Integration tests for Git channel using real Git repositories.

Test setup:
1. Creates a bare Git repository as central "remote"
2. Creates two clones: user_clone and bot_clone
3. User writes messages to user_clone, commits, pushes to bare
4. GitChannel (configured with bot_clone) polls, processes messages, responds
5. User pulls from bare to see bot's responses
"""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.git import GitChannel, GitConfig


def _make_config(local_path: str, **overrides) -> GitConfig:
    """Create GitConfig with sensible defaults for testing."""
    defaults = dict(
        enabled=True,
        local_path=local_path,
        branch="main",
        remote="origin",
        poll_interval_seconds=5,  # Minimum allowed value, but doesn't matter for tests calling _poll() directly
        session_dir_pattern="session-*",
        message_file_pattern="*.md",
        cursor_file=".cursor",
        auto_push=True,
        conflict_resolution="rebase",
    )
    defaults.update(overrides)
    return GitConfig(**defaults)


def _run_git(cwd: Path, *args: str) -> str:
    """Run git command and return stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_git_repo(repo_path: Path, user_name: str = "Test User", user_email: str = "test@example.com") -> None:
    """Initialize a Git repository with basic config."""
    _run_git(repo_path, "init", "--initial-branch=main")
    _run_git(repo_path, "config", "user.name", user_name)
    _run_git(repo_path, "config", "user.email", user_email)


def _create_commit(repo_path: Path, message: str = "test commit") -> None:
    """Create a commit with all changes."""
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", message)


@pytest.fixture
def git_repo_setup(tmp_path: Path):
    """Set up a bare Git repo with two clones for user and bot.

    Returns:
        dict with:
            - bare_path: Path to bare repository
            - user_clone: Path to user's clone
            - bot_clone: Path to bot's clone
    """
    # Create bare repository as central "remote"
    bare_path = tmp_path / "bare.git"
    _run_git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare_path))

    # Create user clone
    user_clone = tmp_path / "user"
    _run_git(tmp_path, "clone", str(bare_path), str(user_clone))
    _init_git_repo(user_clone, "Test User", "user@example.com")

    # Create bot clone
    bot_clone = tmp_path / "bot"
    _run_git(tmp_path, "clone", str(bare_path), str(bot_clone))
    _init_git_repo(bot_clone, "Test Bot", "bot@example.com")

    # Set up remotes
    _run_git(user_clone, "remote", "set-url", "origin", str(bare_path))
    _run_git(bot_clone, "remote", "set-url", "origin", str(bare_path))

    # Create initial commit in user clone and push to establish branch
    (user_clone / "README.md").write_text("# Test Repository")
    _create_commit(user_clone, "Initial commit")
    _run_git(user_clone, "push", "origin", "main")

    # Bot pulls the initial commit
    _run_git(bot_clone, "pull", "origin", "main")

    return {
        "bare_path": bare_path,
        "user_clone": user_clone,
        "bot_clone": bot_clone,
    }


@pytest.fixture
def mock_bus():
    """Mock MessageBus."""
    bus = MagicMock(spec=MessageBus)
    bus.send = AsyncMock()
    return bus


@pytest.fixture
def git_channel_with_repo(git_repo_setup, mock_bus):
    """Create a GitChannel configured to use the bot clone."""
    config = _make_config(local_path=str(git_repo_setup["bot_clone"]))
    channel = GitChannel(config, mock_bus)

    # Ensure channel is stopped after test
    yield {"channel": channel, **git_repo_setup}

    # Cleanup
    if hasattr(channel, '_running') and channel._running:
        asyncio.create_task(channel.stop())


class TestGitChannelIntegration:
    """Integration tests for Git channel with real Git repositories."""

    async def test_basic_message_flow(self, git_channel_with_repo, mock_bus):
        """Test basic message flow: user writes, bot responds."""
        channel = git_channel_with_repo["channel"]
        user_clone = git_channel_with_repo["user_clone"]
        bot_clone = git_channel_with_repo["bot_clone"]

        # 1. User creates a session directory and writes a message
        session_dir = user_clone / "session-001"
        session_dir.mkdir()

        message_file = session_dir / "001-user.md"
        message_file.write_text("Hello, bot!")

        # Commit and push
        _create_commit(user_clone, "User message")
        _run_git(user_clone, "push", "origin", "main")

        # 2. Bot channel polls and processes the message
        # Mock _handle_message to capture the incoming message
        handle_message_calls = []
        original_handle_message = channel._handle_message

        async def mock_handle_message(*args, **kwargs):
            handle_message_calls.append((args, kwargs))
            # Simulate bot response
            response_msg = OutboundMessage(
                channel="git",
                chat_id="session-001",
                content="Hello, user!",
            )
            await channel.send(response_msg)

        channel._handle_message = mock_handle_message

        try:
            # Poll once (instead of starting the polling loop)
            await channel._poll()

            # Verify the message was processed
            assert len(handle_message_calls) == 1
            args, kwargs = handle_message_calls[0]
            assert kwargs["sender_id"] == "session-001:user"
            assert kwargs["content"] == "Hello, bot!"
            assert kwargs["chat_id"] == "session-001"

            # 3. User pulls to get bot's response
            _run_git(user_clone, "pull", "origin", "main")

            # Verify bot created response file
            response_file = session_dir / "002-bot.md"
            assert response_file.exists()
            assert response_file.read_text() == "Hello, user!"

            # Verify git operations happened (commit created)
            bot_log = _run_git(bot_clone, "log", "--oneline")
            assert "nanobot: response in session-001" in bot_log

        finally:
            channel._handle_message = original_handle_message

    async def test_multiple_messages_in_session(self, git_channel_with_repo):
        """Test processing multiple messages in a single session."""
        channel = git_channel_with_repo["channel"]
        user_clone = git_channel_with_repo["user_clone"]

        # Create session
        session_dir = user_clone / "session-multi"
        session_dir.mkdir()

        # Create cursor file to simulate previous read
        (session_dir / ".cursor").write_text("0")

        # Create multiple messages
        (session_dir / "001-user.md").write_text("Message 1")
        (session_dir / "002-user.md").write_text("Message 2")
        (session_dir / "003-user.md").write_text("Message 3")

        _create_commit(user_clone, "Multiple user messages")
        _run_git(user_clone, "push", "origin", "main")

        # Track processed messages
        processed_messages = []
        original_handle_message = channel._handle_message

        async def mock_handle_message(*args, **kwargs):
            processed_messages.append(kwargs["content"])
            # Auto-respond to each message
            response_msg = OutboundMessage(
                channel="git",
                chat_id="session-multi",
                content=f"Response to: {kwargs['content']}",
            )
            await channel.send(response_msg)

        channel._handle_message = mock_handle_message

        try:
            await channel._poll()

            # Should process all 3 messages
            assert len(processed_messages) == 3
            assert processed_messages == ["Message 1", "Message 2", "Message 3"]

            # Give time for async pushes to complete
            await asyncio.sleep(0.2)

            # User pulls to get bot's responses
            _run_git(user_clone, "pull", "origin", "main")

            # Should have 3 response files in user clone
            for i in range(1, 4):
                response_file = session_dir / f"{i+3:03d}-bot.md"
                assert response_file.exists()

            # Cursor should be updated to 3 in bot's clone
            # Note: cursor file is not automatically committed/pushed
            bot_session_dir = git_channel_with_repo["bot_clone"] / "session-multi"
            cursor_file = bot_session_dir / ".cursor"
            assert cursor_file.exists()
            assert cursor_file.read_text() == "3"

        finally:
            channel._handle_message = original_handle_message

    async def test_auto_push_disabled(self, git_channel_with_repo):
        """Test that auto_push=False prevents automatic pushing."""
        # Recreate channel with auto_push=False
        bot_clone = git_channel_with_repo["bot_clone"]
        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.send = AsyncMock()

        config = _make_config(
            local_path=str(bot_clone),
            auto_push=False,
            poll_interval_seconds=5,  # Longer to avoid auto-polling
        )
        channel = GitChannel(config, mock_bus)

        user_clone = git_channel_with_repo["user_clone"]

        # Create message
        session_dir = user_clone / "session-nopush"
        session_dir.mkdir()
        (session_dir / "001-user.md").write_text("Test no auto-push")

        _create_commit(user_clone, "Test message")
        _run_git(user_clone, "push", "origin", "main")

        # Mock send to capture what would be sent
        sent_messages = []
        original_send = channel.send

        async def mock_send(msg):
            sent_messages.append(msg)
            # Call original but with auto_push=False
            await original_send(msg)

        channel.send = mock_send

        try:
            # Process the message
            original_handle_message = channel._handle_message

            async def mock_handle_message(*args, **kwargs):
                response = OutboundMessage(
                    channel="git",
                    chat_id="session-nopush",
                    content="Response without auto-push",
                )
                await channel.send(response)

            channel._handle_message = mock_handle_message

            await channel._poll()

            # Should have sent a message
            assert len(sent_messages) == 1

            # Check that file was created locally but not pushed
            response_file = bot_clone / "session-nopush" / "002-bot.md"
            assert response_file.exists()

            # But changes should not be in remote (user clone)
            # User pulls - should not get the response
            _run_git(user_clone, "pull", "origin", "main")
            user_response_file = session_dir / "002-bot.md"
            assert not user_response_file.exists()

        finally:
            channel.send = original_send
            channel._handle_message = original_handle_message

    async def test_custom_session_pattern(self, git_channel_with_repo):
        """Test custom session directory pattern."""
        bot_clone = git_channel_with_repo["bot_clone"]
        user_clone = git_channel_with_repo["user_clone"]
        mock_bus = MagicMock(spec=MessageBus)
        mock_bus.send = AsyncMock()

        # Configure channel with custom pattern
        config = _make_config(
            local_path=str(bot_clone),
            session_dir_pattern="chat-*",
        )
        channel = GitChannel(config, mock_bus)

        # Create directory matching custom pattern
        chat_dir = user_clone / "chat-123"
        chat_dir.mkdir()
        (chat_dir / "001-user.md").write_text("Hello from chat")

        # Also create directory with default pattern (should be ignored)
        session_dir = user_clone / "session-456"
        session_dir.mkdir()
        (session_dir / "001-user.md").write_text("This should be ignored")

        _create_commit(user_clone, "Test custom pattern")
        _run_git(user_clone, "push", "origin", "main")

        processed_chats = []
        original_handle_message = channel._handle_message

        async def mock_handle_message(*args, **kwargs):
            processed_chats.append(kwargs["chat_id"])

        channel._handle_message = mock_handle_message

        try:
            await channel._poll()

            # Should only process chat-123, not session-456
            assert len(processed_chats) == 1
            assert processed_chats[0] == "chat-123"

        finally:
            channel._handle_message = original_handle_message

    async def test_message_numbering(self, git_channel_with_repo):
        """Test correct message numbering across multiple responses."""
        channel = git_channel_with_repo["channel"]
        user_clone = git_channel_with_repo["user_clone"]

        session_dir = user_clone / "session-numbering"
        session_dir.mkdir()

        # Create existing messages
        (session_dir / "001-user.md").write_text("First")
        (session_dir / "005-user.md").write_text("With gap")  # Intentional gap

        _create_commit(user_clone, "Existing messages")
        _run_git(user_clone, "push", "origin", "main")

        response_count = 0
        original_handle_message = channel._handle_message

        async def mock_handle_message(*args, **kwargs):
            nonlocal response_count
            response_count += 1
            # Bot responds to each message
            response_msg = OutboundMessage(
                channel="git",
                chat_id="session-numbering",
                content=f"Response {response_count}",
            )
            await channel.send(response_msg)

        channel._handle_message = mock_handle_message

        try:
            await channel._poll()

            # Should process 2 messages
            assert response_count == 2

            # Bot should create files 006-bot.md and 007-bot.md
            # (next after highest existing number 005)
            response1 = session_dir / "006-bot.md"
            response2 = session_dir / "007-bot.md"

            # Pull to see bot's responses
            _run_git(user_clone, "pull", "origin", "main")

            assert response1.exists()
            assert response2.exists()

        finally:
            channel._handle_message = original_handle_message
