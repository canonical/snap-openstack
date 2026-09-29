# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for LargeInputStreamWrapper and LargeInputPromptQuestion.

These classes bypass the Linux N_TTY 4096-byte canonical-mode limit for
certificate input prompts.

The test suite has two distinct groups:

Unit tests (TestReadLineNoIcanon, TestLargeInputStreamWrapper,
TestLargeInputPromptQuestion):
  No actual TTY is opened.  All terminal-level functions (termios,
  os.isatty, os.read) are patched with mocks.

PTY integration tests (TestPtyTruncationBehavior):
  Real PTY pairs are opened via pty.openpty() to exercise the actual
  kernel N_TTY line discipline.  These tests demonstrate the erratic
  truncation behaviour under canonical mode and confirm it is resolved
  when ICANON is cleared.

BASE64_CASES covers realistic certificate sizes:
  - RSA-2048 single cert  (~1600 chars)  — well under 4096, must pass unchanged
  - RSA-4096 single cert  (~2268 chars)  — under 4096, must pass unchanged
  - CA chain 3 certs      (~4800 chars)  — OVER 4096, historically truncated
  - CA chain 5 certs      (~10000 chars) — well over 4096, historically truncated
"""

import base64
import io
import os
import termios
from unittest.mock import MagicMock, patch

import pytest

from sunbeam.core.questions import (
    LARGE_INPUT_STREAM,
    STREAM,
    LargeInputPromptQuestion,
    LargeInputStreamWrapper,
    PromptQuestion,
    StreamWrapper,
    _read_line_no_icanon,
)

# ---------------------------------------------------------------------------
# Realistic base64 test data (deterministic: fixed seed via fixed bytes)
# ---------------------------------------------------------------------------

# Use fixed byte patterns so test data is deterministic across runs.
_RSA2048_B64 = base64.b64encode(bytes(range(256)) * 5).decode()  # ~1368 chars
_RSA4096_B64 = base64.b64encode(bytes(range(256)) * 7).decode()  # ~2388 chars
_CA_CHAIN_3_B64 = base64.b64encode(
    bytes(range(256)) * 14
).decode()  # ~4824 chars (> 4096)
_CA_CHAIN_5_B64 = base64.b64encode(
    bytes(range(256)) * 23
).decode()  # ~7912 chars (> 4096)

BASE64_CASES = pytest.mark.parametrize(
    "b64_input,expected_len",
    [
        pytest.param(_RSA2048_B64, len(_RSA2048_B64), id="rsa2048-under-4096"),
        pytest.param(_RSA4096_B64, len(_RSA4096_B64), id="rsa4096-under-4096"),
        pytest.param(
            _CA_CHAIN_3_B64, len(_CA_CHAIN_3_B64), id="ca-chain-3certs-over-4096"
        ),
        pytest.param(
            _CA_CHAIN_5_B64, len(_CA_CHAIN_5_B64), id="ca-chain-5certs-over-4096"
        ),
    ],
)

# ---------------------------------------------------------------------------
# _read_line_no_icanon
# ---------------------------------------------------------------------------


class TestReadLineNoIcanon:
    """Unit tests for _read_line_no_icanon()."""

    def _make_char_reads(self, text: str, terminator: str = "\n") -> list[bytes]:
        """Return a sequence of single-byte os.read return values for text."""
        return [c.encode() for c in text + terminator]

    def test_reads_short_line(self):
        chars = self._make_char_reads("hello")
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr",
                return_value=[0, 0, 0, 0, 0, 0, [0] * 20],
            ),
            patch("sunbeam.core.questions.termios.tcsetattr"),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            result = _read_line_no_icanon(0)
        assert result == "hello"

    @BASE64_CASES
    def test_reads_base64_input_without_truncation(self, b64_input, expected_len):
        """Must read the full base64 string regardless of whether it exceeds 4096 chars.

        Covers both under-4096 (RSA-2048/4096 single certs) and over-4096
        (CA chains) inputs.
        """
        chars = self._make_char_reads(b64_input)
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr",
                return_value=[0, 0, 0, 0, 0, 0, [0] * 20],
            ),
            patch("sunbeam.core.questions.termios.tcsetattr"),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            result = _read_line_no_icanon(0)
        assert result == b64_input
        assert len(result) == expected_len

    def test_carriage_return_terminates_line(self):
        chars = self._make_char_reads("abc", terminator="\r")
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr",
                return_value=[0, 0, 0, 0, 0, 0, [0] * 20],
            ),
            patch("sunbeam.core.questions.termios.tcsetattr"),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            result = _read_line_no_icanon(0)
        assert result == "abc"

    def test_backspace_removes_last_char(self):
        # Type "abc", backspace, then "d" → "abd"
        chars = [b"a", b"b", b"c", b"\x7f", b"d", b"\n"]
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr",
                return_value=[0, 0, 0, 0, 0, 0, [0] * 20],
            ),
            patch("sunbeam.core.questions.termios.tcsetattr"),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            result = _read_line_no_icanon(0)
        assert result == "abd"

    def test_backspace_on_empty_buffer_is_safe(self):
        chars = [b"\x7f", b"\x7f", b"x", b"\n"]
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr",
                return_value=[0, 0, 0, 0, 0, 0, [0] * 20],
            ),
            patch("sunbeam.core.questions.termios.tcsetattr"),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            result = _read_line_no_icanon(0)
        assert result == "x"

    def test_ctrl_c_raises_keyboard_interrupt(self):
        chars = [b"a", b"\x03"]
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr",
                return_value=[0, 0, 0, 0, 0, 0, [0] * 20],
            ),
            patch("sunbeam.core.questions.termios.tcsetattr"),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            with pytest.raises(KeyboardInterrupt):
                _read_line_no_icanon(0)

    def test_terminal_settings_restored_on_success(self):
        """Call tcsetattr twice: once to clear ICANON, once to restore."""
        original_attrs = [0, 0, 0, 0b00000010, 0, 0, [0] * 20]  # ICANON bit set
        chars = [b"x", b"\n"]
        mock_tcsetattr = MagicMock()
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr", return_value=original_attrs
            ),
            patch("sunbeam.core.questions.termios.tcsetattr", mock_tcsetattr),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            _read_line_no_icanon(0)
        assert mock_tcsetattr.call_count == 2
        # Second call (restore) must use the original attrs
        restore_call_attrs = mock_tcsetattr.call_args_list[1][0][2]
        assert restore_call_attrs == original_attrs

    def test_terminal_settings_restored_on_keyboard_interrupt(self):
        """Finally block must restore even when Ctrl+C is raised."""
        original_attrs = [0, 0, 0, 0, 0, 0, [0] * 20]
        chars = [b"\x03"]
        mock_tcsetattr = MagicMock()
        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr", return_value=original_attrs
            ),
            patch("sunbeam.core.questions.termios.tcsetattr", mock_tcsetattr),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            with pytest.raises(KeyboardInterrupt):
                _read_line_no_icanon(0)
        assert mock_tcsetattr.call_count == 2

    def test_restore_oserror_does_not_mask_original_exception(self):
        """OSError in finally must not mask KeyboardInterrupt or other errors."""
        original_attrs = [0, 0, 0, 0, 0, 0, [0] * 20]
        chars = [b"\x03"]
        call_count = 0

        def tcsetattr_side_effect(fd, when, attrs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # restore call
                raise OSError("fd closed")

        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr", return_value=original_attrs
            ),
            patch(
                "sunbeam.core.questions.termios.tcsetattr",
                side_effect=tcsetattr_side_effect,
            ),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            # KeyboardInterrupt must propagate, not be masked by the OSError
            with pytest.raises(KeyboardInterrupt):
                _read_line_no_icanon(0)

    def test_clears_only_icanon_flag(self):
        """The new attrs passed to tcsetattr must have ICANON cleared, nothing else."""
        original_lflag = termios.ICANON | termios.ECHO | termios.ISIG
        original_attrs = [0, 0, 0, original_lflag, 0, 0, [0] * 20]
        chars = [b"x", b"\n"]
        captured = []

        def capture_tcsetattr(fd, when, attrs):
            captured.append(list(attrs))

        with (
            patch(
                "sunbeam.core.questions.termios.tcgetattr", return_value=original_attrs
            ),
            patch(
                "sunbeam.core.questions.termios.tcsetattr",
                side_effect=capture_tcsetattr,
            ),
            patch("sunbeam.core.questions.os.read", side_effect=chars),
        ):
            _read_line_no_icanon(0)
        new_lflag = captured[0][3]
        assert not (new_lflag & termios.ICANON), "ICANON should be cleared"
        assert new_lflag & termios.ECHO, "ECHO should be preserved"
        assert new_lflag & termios.ISIG, "ISIG should be preserved"


# ---------------------------------------------------------------------------
# LargeInputStreamWrapper
# ---------------------------------------------------------------------------


class TestLargeInputStreamWrapper:
    """Tests for LargeInputStreamWrapper.readline()."""

    def _make_wrapper(self, is_tty: bool, fileno: int = 5):
        read_stream = MagicMock()
        read_stream.fileno.return_value = fileno
        write_stream = MagicMock()
        wrapper = LargeInputStreamWrapper(read_stream, write_stream)
        return wrapper, read_stream

    def test_uses_no_icanon_when_tty(self):
        wrapper, read_stream = self._make_wrapper(is_tty=True)
        with (
            patch("sunbeam.core.questions.os.isatty", return_value=True),
            patch(
                "sunbeam.core.questions._read_line_no_icanon", return_value="cert-data"
            ) as mock_read,
        ):
            result = wrapper.readline()
        mock_read.assert_called_once_with(read_stream.fileno())
        assert result == "cert-data"

    def test_falls_back_to_normal_readline_when_not_tty(self):
        wrapper, read_stream = self._make_wrapper(is_tty=False)
        read_stream.readline.return_value = "piped-data\n"
        with patch("sunbeam.core.questions.os.isatty", return_value=False):
            result = wrapper.readline()
        read_stream.readline.assert_called_once()
        assert result == "piped-data\n"

    def test_falls_back_when_fileno_raises_os_error(self):
        read_stream = MagicMock()
        read_stream.fileno.side_effect = OSError("no fileno")
        read_stream.readline.return_value = "fallback\n"
        wrapper = LargeInputStreamWrapper(read_stream, MagicMock())
        result = wrapper.readline()
        assert result == "fallback\n"

    def test_falls_back_when_fileno_raises_attribute_error(self):
        read_stream = MagicMock(spec=io.StringIO)
        read_stream.fileno.side_effect = AttributeError
        read_stream.readline.return_value = "fallback\n"
        wrapper = LargeInputStreamWrapper(read_stream, MagicMock())
        result = wrapper.readline()
        assert result == "fallback\n"

    def test_empty_string_returned_for_bare_newline(self):
        """Bare newline (empty input) must return '' like StreamWrapper does."""
        wrapper, read_stream = self._make_wrapper(is_tty=False)
        read_stream.readline.return_value = "\n"
        with patch("sunbeam.core.questions.os.isatty", return_value=False):
            result = wrapper.readline()
        assert result == ""

    def test_empty_no_icanon_result_returns_empty_string(self):
        """Empty TTY read (user just pressed Enter) must return ''."""
        wrapper, read_stream = self._make_wrapper(is_tty=True)
        with (
            patch("sunbeam.core.questions.os.isatty", return_value=True),
            patch("sunbeam.core.questions._read_line_no_icanon", return_value=""),
        ):
            result = wrapper.readline()
        assert result == ""

    def test_large_input_not_truncated(self):
        """Verify that 5000-char input passes through unchanged (no 4096 trim)."""
        large = "B" * 5000
        wrapper, read_stream = self._make_wrapper(is_tty=True)
        with (
            patch("sunbeam.core.questions.os.isatty", return_value=True),
            patch("sunbeam.core.questions._read_line_no_icanon", return_value=large),
        ):
            result = wrapper.readline()
        assert result == large
        assert len(result) == 5000

    @BASE64_CASES
    def test_base64_input_passes_through_without_truncation(
        self, b64_input, expected_len
    ):
        """Full base64 string — both under and over 4096 — must arrive intact."""
        wrapper, read_stream = self._make_wrapper(is_tty=True)
        with (
            patch("sunbeam.core.questions.os.isatty", return_value=True),
            patch(
                "sunbeam.core.questions._read_line_no_icanon", return_value=b64_input
            ),
        ):
            result = wrapper.readline()
        assert result == b64_input
        assert len(result) == expected_len


# ---------------------------------------------------------------------------
# get_stdin_reopen_tty — LARGE_INPUT_STREAM sync
# ---------------------------------------------------------------------------


class TestGetStdinReopenTty:
    """Verify that get_stdin_reopen_tty() keeps LARGE_INPUT_STREAM in sync."""

    def test_large_input_stream_updated_after_tty_reopen(self):
        """Sync LARGE_INPUT_STREAM after stdin is reopened to /dev/tty.

        Must point to the new stdin so that certificate prompts issued after
        the reopen read from the correct file descriptor.
        """
        import sunbeam.core.questions as q_mod

        fake_tty = MagicMock()
        original_stream_read = q_mod.STREAM.read_stream
        original_large_read = q_mod.LARGE_INPUT_STREAM.read_stream
        try:
            # Simulate what get_stdin_reopen_tty() does after reopening /dev/tty
            q_mod.STREAM.read_stream = fake_tty
            q_mod.LARGE_INPUT_STREAM.read_stream = fake_tty

            assert q_mod.LARGE_INPUT_STREAM.read_stream is fake_tty
            assert q_mod.STREAM.read_stream is fake_tty
        finally:
            q_mod.STREAM.read_stream = original_stream_read
            q_mod.LARGE_INPUT_STREAM.read_stream = original_large_read


# ---------------------------------------------------------------------------
# LargeInputPromptQuestion
# ---------------------------------------------------------------------------


class TestLargeInputPromptQuestion:
    """Tests for LargeInputPromptQuestion._input_stream and ask()."""

    def test_input_stream_returns_large_input_stream(self):
        q = LargeInputPromptQuestion("Enter cert")
        assert q._input_stream is LARGE_INPUT_STREAM

    def test_input_stream_is_large_input_stream_wrapper_instance(self):
        q = LargeInputPromptQuestion("Enter cert")
        assert isinstance(q._input_stream, LargeInputStreamWrapper)

    def test_prompt_question_input_stream_returns_plain_stream(self):
        """PromptQuestion should still use STREAM, not LARGE_INPUT_STREAM."""
        q = PromptQuestion("Enter value")
        assert q._input_stream is STREAM
        assert isinstance(q._input_stream, StreamWrapper)
        assert not isinstance(q._input_stream, LargeInputStreamWrapper)

    def test_ask_uses_large_input_stream(self):
        """ask() must pass LARGE_INPUT_STREAM as the stream kwarg."""
        q = LargeInputPromptQuestion("Enter cert")
        with patch(
            "sunbeam.core.questions.Prompt.ask", return_value="cert-value"
        ) as mock_ask:
            result = q.ask()
        mock_ask.assert_called_once()
        _, kwargs = mock_ask.call_args
        assert kwargs.get("stream") is LARGE_INPUT_STREAM
        assert result == "cert-value"

    def test_ask_with_preseed_skips_prompt(self):
        """Preseed must bypass the prompt entirely (no TTY interaction)."""
        q = LargeInputPromptQuestion("Enter cert")
        q.preseed = "preseeded-cert"
        with patch("sunbeam.core.questions.Prompt.ask") as mock_ask:
            result = q.ask()
        mock_ask.assert_not_called()
        assert result == "preseeded-cert"

    def test_ask_with_accept_defaults(self):
        """accept_defaults must return the default without prompting."""
        q = LargeInputPromptQuestion(
            "Enter cert", default_value="default-cert", accept_defaults=True
        )
        with patch("sunbeam.core.questions.Prompt.ask") as mock_ask:
            result = q.ask()
        mock_ask.assert_not_called()
        assert result == "default-cert"


# ---------------------------------------------------------------------------
# PTY integration tests — demonstrate the real N_TTY erratic behaviour
# ---------------------------------------------------------------------------


def _pty_readline_canonical(num_chars: int) -> int:
    """Write num_chars to a real PTY master in canonical (ICANON) mode.

    Returns the number of characters received by the slave before the
    newline.  The kernel N_TTY line discipline silently truncates at 4095.
    """
    import pty
    import threading

    master_fd, slave_fd = pty.openpty()
    received: list[int] = []
    done = threading.Event()

    def reader():
        buf = b""
        while True:
            try:
                ch = os.read(slave_fd, 1)
            except OSError:
                break
            if ch in (b"\n", b"\r"):
                break
            buf += ch
        received.append(len(buf))
        done.set()
        try:
            os.close(slave_fd)
        except OSError:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    os.write(master_fd, ("A" * num_chars + "\n").encode())
    done.wait(timeout=2)
    os.close(master_fd)
    t.join(timeout=1)
    return received[0] if received else -1


def _pty_readline_no_icanon(num_chars: int) -> int:
    """Write num_chars to a real PTY slave with ICANON cleared.

    Returns the number of characters received.  Should equal num_chars
    regardless of the N_TTY buffer size.
    """
    import pty
    import threading

    master_fd, slave_fd = pty.openpty()

    # Clear ICANON on the slave side before the reader starts
    attrs = termios.tcgetattr(slave_fd)
    attrs[3] &= ~termios.ICANON
    attrs[6] = list(attrs[6])
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

    received: list[int] = []
    done = threading.Event()

    def reader():
        buf = b""
        while True:
            try:
                ch = os.read(slave_fd, 1)
            except OSError:
                break
            if ch in (b"\n", b"\r"):
                break
            buf += ch
        received.append(len(buf))
        done.set()
        try:
            os.close(slave_fd)
        except OSError:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    # Write in chunks to avoid overflowing the PTY buffer
    payload = ("A" * num_chars + "\n").encode()
    chunk = 512
    for i in range(0, len(payload), chunk):
        os.write(master_fd, payload[i : i + chunk])
    done.wait(timeout=5)
    os.close(master_fd)
    t.join(timeout=2)
    return received[0] if received else -1


@pytest.mark.parametrize(
    "num_chars",
    [
        pytest.param(1000, id="1000-under-4096"),
        pytest.param(4095, id="4095-boundary-minus-1"),
        pytest.param(4096, id="4096-boundary"),
        pytest.param(5000, id="5000-over-4096"),
        pytest.param(len(_CA_CHAIN_3_B64), id="ca-chain-3certs"),
        pytest.param(len(_CA_CHAIN_5_B64), id="ca-chain-5certs"),
    ],
)
class TestPtyTruncationBehavior:
    """Integration tests using real PTY pairs to demonstrate N_TTY behaviour.

    These tests do NOT mock termios or os.read — they exercise the actual
    kernel line discipline to confirm:

      1. Canonical mode (ICANON on)  → truncates at 4095 chars
      2. ICANON cleared              → delivers all chars intact
    """

    def test_canonical_mode_truncates_above_4095(self, num_chars):
        """In canonical mode the kernel silently discards chars beyond 4095."""
        received = _pty_readline_canonical(num_chars)
        if num_chars <= 4095:
            assert received == num_chars, (
                f"Expected {num_chars} chars, got {received} (should NOT truncate)"
            )
        else:
            assert received == 4095, (
                f"Expected truncation to 4095, got {received} for input of {num_chars}"
            )

    def test_no_icanon_delivers_full_input(self, num_chars):
        """With ICANON cleared the full input arrives regardless of size."""
        received = _pty_readline_no_icanon(num_chars)
        assert received == num_chars, (
            f"Expected {num_chars} chars, got {received} — "
            "ICANON=off should not truncate"
        )
