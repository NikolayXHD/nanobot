"""Tests for Git channel."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.git import GitChannel, GitConfig


def _make_config(**overrides) -> GitConfig:
    """Create GitConfig for testing."""
    defaults = dict(
        enabled=True,
        local_path="/tmp/test_repo",
        branch="main",
        remote="origin",
        poll_interval_seconds=5,  # Minimum allowed value
        session_dir_pattern="session-*",
        message_file_pattern="*.md",
        cursor_file=".cursor",
        auto_push=True,
        conflict_resolution="rebase",
    )
    defaults.update(overrides)
    return GitConfig(**defaults)


@pytest.fixture
def mock_bus():
    """Mock MessageBus."""
    bus = MagicMock(spec=MessageBus)
    bus.send = AsyncMock()
    return bus


@pytest.fixture
def mock_git_channel(tmp_path, mock_bus):
    """Create a GitChannel with a temporary repository path."""
    config = _make_config(local_path=str(tmp_path))
    channel = GitChannel(config, mock_bus)
    return channel


class TestGitConfig:
    """Test GitConfig validation."""

    def test_default_config(self):
        config = GitConfig()
        assert config.enabled is False
        assert config.local_path == ""
        assert config.branch == "main"
        assert config.remote == "origin"
        assert config.poll_interval_seconds == 30
        assert config.session_dir_pattern == "session-*"
        assert config.message_file_pattern == "*.md"
        assert config.cursor_file == ".cursor"
        assert config.auto_push is True
        assert config.conflict_resolution == "rebase"

    def test_config_with_custom_values(self):
        config = GitConfig(
            enabled=True,
            local_path="/home/user/repo",
            branch="develop",
            remote="upstream",
            poll_interval_seconds=60,
            session_dir_pattern="chat-*",
            message_file_pattern="*.txt",
            cursor_file=".lastread",
            auto_push=False,
            conflict_resolution="merge",
        )
        assert config.enabled is True
        assert config.local_path == "/home/user/repo"
        assert config.branch == "develop"
        assert config.remote == "upstream"
        assert config.poll_interval_seconds == 60
        assert config.session_dir_pattern == "chat-*"
        assert config.message_file_pattern == "*.txt"
        assert config.cursor_file == ".lastread"
        assert config.auto_push is False
        assert config.conflict_resolution == "merge"


class TestGitChannelInitialization:
    """Test GitChannel initialization."""

    def test_init_with_dict_config(self, mock_bus):
        config_dict = {
            "enabled": True,
            "local_path": "/tmp/repo",
            "branch": "main",
        }
        channel = GitChannel(config_dict, mock_bus)
        assert channel.config.enabled is True
        assert channel.config.local_path == "/tmp/repo"
        assert channel.config.branch == "main"
        assert channel.name == "git"
        assert channel.display_name == "Git"

    def test_init_with_gitconfig(self, mock_bus):
        config = _make_config()
        channel = GitChannel(config, mock_bus)
        assert channel.config == config
        assert channel._repo_path == Path(config.local_path).expanduser().resolve()
        assert channel._session_glob == config.session_dir_pattern
        assert channel._message_glob == config.message_file_pattern
        assert channel._cursor_file == config.cursor_file
        assert channel._running is False

    def test_default_config_method(self):
        default_config = GitChannel.default_config()
        assert isinstance(default_config, dict)
        assert "enabled" in default_config
        assert default_config["enabled"] is False


class TestGitChannelStartStop:
    """Test start and stop methods."""

    @pytest.mark.asyncio
    async def test_start_when_disabled(self, mock_bus):
        config = _make_config(enabled=False)
        channel = GitChannel(config, mock_bus)
        # Should return immediately without entering loop
        with patch.object(channel, '_running', False):
            await channel.start()
        # No assertion needed - if we get here, test passed

    @pytest.mark.asyncio
    async def test_start_when_path_does_not_exist(self, mock_bus):
        config = _make_config(local_path="/nonexistent/path")
        channel = GitChannel(config, mock_bus)
        # Should return immediately after logging error
        with patch.object(channel, '_running', False):
            await channel.start()

    @pytest.mark.asyncio
    async def test_start_when_not_git_repo(self, tmp_path, mock_bus):
        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, mock_bus)
        # Should return immediately after logging error
        with patch.object(channel, '_running', False):
            await channel.start()

    @pytest.mark.asyncio
    async def test_start_success(self, tmp_path, mock_bus):
        # Create a git repository
        import subprocess
        subprocess.run(["git", "init", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test User"], check=True)

        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, mock_bus)

        # Mock the polling loop to run once and then stop
        with patch.object(channel, '_poll', AsyncMock()) as mock_poll, \
             patch('asyncio.sleep', AsyncMock()) as mock_sleep:
            # Make sleep stop the loop after first iteration
            def stop_loop(*args, **kwargs):
                channel._running = False
            mock_sleep.side_effect = stop_loop

            await channel.start()
            # Should call _poll at least once
            mock_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop(self, mock_git_channel):
        mock_git_channel._running = True
        await mock_git_channel.stop()
        assert mock_git_channel._running is False


class TestGitChannelSend:
    """Test send method."""

    @pytest.mark.asyncio
    async def test_send_when_disabled(self, mock_git_channel):
        mock_git_channel.config.enabled = False
        msg = OutboundMessage(channel="git", chat_id="session-1", content="Hello")
        # Should return without doing anything
        await mock_git_channel.send(msg)
        # No assertion needed - if we get here, test passed

    @pytest.mark.asyncio
    async def test_send_session_dir_not_exist(self, tmp_path, mock_bus):
        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, mock_bus)
        channel.config.enabled = True

        msg = OutboundMessage(channel="git", chat_id="session-1", content="Hello")
        # Should return after logging error
        await channel.send(msg)
        # No assertion needed

    @pytest.mark.asyncio
    async def test_send_success(self, tmp_path, mock_bus):
        # Create session directory
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()

        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, mock_bus)
        channel.config.enabled = True

        # Mock git commands
        with patch.object(channel, '_git_add', AsyncMock()) as mock_add, \
             patch.object(channel, '_git_commit', AsyncMock()) as mock_commit, \
             patch.object(channel, '_git_push', AsyncMock()) as mock_push:

            msg = OutboundMessage(channel="git", chat_id="session-1", content="Hello world")
            await channel.send(msg)

            # Check that file was created
            expected_file = session_dir / "001-bot.md"
            assert expected_file.exists()
            assert expected_file.read_text() == "Hello world"

            # Check git commands called
            mock_add.assert_called_once_with(str(expected_file))
            mock_commit.assert_called_once()
            mock_push.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_without_auto_push(self, tmp_path, mock_bus):
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()

        config = _make_config(local_path=str(tmp_path), auto_push=False)
        channel = GitChannel(config, mock_bus)
        channel.config.enabled = True

        with patch.object(channel, '_git_add', AsyncMock()), \
             patch.object(channel, '_git_commit', AsyncMock()), \
             patch.object(channel, '_git_push', AsyncMock()) as mock_push:

            msg = OutboundMessage(channel="git", chat_id="session-1", content="Test")
            await channel.send(msg)

            # Should not push
            mock_push.assert_not_called()


class TestGitChannelHelpers:
    """Test helper methods."""

    @pytest.mark.asyncio
    async def test_run_git_command(self, tmp_path):
        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, MagicMock())

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"output", b"")
            mock_proc.returncode = 0
            mock_subprocess.return_value = mock_proc

            result = await channel._run_git_command(["status"])
            assert result == "output"
            mock_subprocess.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_git_command_failure(self, tmp_path):
        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, MagicMock())

        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"error")
            mock_proc.returncode = 1
            mock_subprocess.return_value = mock_proc

            with pytest.raises(RuntimeError, match="Git command failed"):
                await channel._run_git_command(["status"])

    def test_find_sessions(self, tmp_path):
        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, MagicMock())

        # Create some directories
        (tmp_path / "session-1").mkdir()
        (tmp_path / "session-2").mkdir()
        (tmp_path / "other").mkdir()
        (tmp_path / "session-3-file").touch()

        sessions = channel._find_sessions()
        session_names = {s.name for s in sessions}
        assert session_names == {"session-1", "session-2"}

    def test_find_sessions_custom_pattern(self, tmp_path):
        config = _make_config(local_path=str(tmp_path), session_dir_pattern="chat-*")
        channel = GitChannel(config, MagicMock())

        (tmp_path / "chat-1").mkdir()
        (tmp_path / "session-1").mkdir()

        sessions = channel._find_sessions()
        assert len(sessions) == 1
        assert sessions[0].name == "chat-1"

    def test_next_message_number(self, tmp_path):
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()

        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, MagicMock())

        # No message files yet
        assert channel._next_message_number(session_dir) == 1

        # Create some message files
        (session_dir / "001-user.md").write_text("Hello")
        (session_dir / "002-bot.md").write_text("Hi")
        (session_dir / "010-user.md").write_text("Hey")

        assert channel._next_message_number(session_dir) == 11

        # Test with non-matching files
        (session_dir / "readme.txt").write_text("Ignore")
        (session_dir / "003-admin.txt").write_text("Ignore")

        assert channel._next_message_number(session_dir) == 11  # Only .md files count


class TestGitChannelPolling:
    """Test polling and message processing."""

    @pytest.mark.asyncio
    async def test_poll_with_no_sessions(self, mock_git_channel):
        with patch.object(mock_git_channel, '_git_checkout_branch', AsyncMock()), \
             patch.object(mock_git_channel, '_git_pull_rebase', AsyncMock(return_value=True)), \
             patch.object(mock_git_channel, '_find_sessions', return_value=[]):

            await mock_git_channel._poll()
            # Should complete without errors

    @pytest.mark.asyncio
    async def test_process_session_no_messages(self, tmp_path, mock_git_channel):
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()

        # No cursor file
        await mock_git_channel._process_session(session_dir)
        # Should do nothing

    @pytest.mark.asyncio
    async def test_process_session_with_new_messages(self, tmp_path, mock_bus):
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()

        # Create message files
        (session_dir / "001-user.md").write_text("Message 1")
        (session_dir / "002-bot.md").write_text("Message 2")

        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, mock_bus)
        channel.config.enabled = True

        # Mock the bus handling
        with patch.object(channel, '_handle_message', AsyncMock()) as mock_handle:
            await channel._process_session(session_dir)

            # Should call _handle_message twice
            assert mock_handle.call_count == 2

            # Check calls
            calls = mock_handle.call_args_list
            assert calls[0][1]['sender_id'] == "session-1:user"
            assert calls[0][1]['content'] == "Message 1"
            assert calls[0][1]['chat_id'] == "session-1"

            assert calls[1][1]['sender_id'] == "session-1:bot"
            assert calls[1][1]['content'] == "Message 2"
            assert calls[1][1]['chat_id'] == "session-1"

            # Cursor file should be updated
            cursor_file = session_dir / ".cursor"
            assert cursor_file.exists()
            assert cursor_file.read_text() == "2"

    @pytest.mark.asyncio
    async def test_process_session_with_cursor(self, tmp_path, mock_bus):
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()

        # Create cursor file
        (session_dir / ".cursor").write_text("1")

        # Create message files
        (session_dir / "001-user.md").write_text("Message 1")
        (session_dir / "002-bot.md").write_text("Message 2")
        (session_dir / "003-user.md").write_text("Message 3")

        config = _make_config(local_path=str(tmp_path))
        channel = GitChannel(config, mock_bus)

        with patch.object(channel, '_handle_message', AsyncMock()) as mock_handle:
            await channel._process_session(session_dir)

            # Should only process messages 2 and 3 (after cursor=1)
            assert mock_handle.call_count == 2
            calls = mock_handle.call_args_list
            assert calls[0][1]['metadata']['number'] == 2
            assert calls[1][1]['metadata']['number'] == 3

            # Cursor updated to 3
            cursor_file = session_dir / ".cursor"
            assert cursor_file.read_text() == "3"
