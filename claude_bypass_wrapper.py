#!/usr/bin/env python3
"""
Spawn `claude --permission-mode bypassPermissions` under a PTY and auto-accept
the "Bypass Permissions" confirmation prompt that Claude Code shows on every
TUI startup. Then become a transparent passthrough between the user's terminal
and the spawned Claude TUI.

Used by research_mvp runtime so each tmux pane (leader/researcher/trainer)
gets through the prompt without a human pressing "2 + Enter".
"""
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import fcntl
import tty


CHILD_ARGV = ["claude", "--permission-mode", "bypassPermissions", *sys.argv[1:]]
ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]")
ACCEPT_MARKER = b"Iaccept"


def set_winsize(fd: int) -> None:
    try:
        s = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, s)
    except Exception:
        pass


def main() -> int:
    pid, master_fd = pty.fork()
    if pid == 0:
        os.execvp(CHILD_ARGV[0], CHILD_ARGV)
        os._exit(127)

    set_winsize(master_fd)

    def _on_winch(_sig, _frm):
        set_winsize(master_fd)

    signal.signal(signal.SIGWINCH, _on_winch)

    in_fd = sys.stdin.fileno()
    out_fd = sys.stdout.fileno()
    is_tty = os.isatty(in_fd)

    old_attrs = None
    if is_tty:
        try:
            old_attrs = termios.tcgetattr(in_fd)
            tty.setraw(in_fd)
        except Exception:
            old_attrs = None

    accepted = False
    seen_buf = b""
    SEEN_TAIL_MAX = 4096

    try:
        while True:
            try:
                rlist, _, _ = select.select([master_fd, in_fd] if is_tty else [master_fd], [], [], 0.5)
            except (InterruptedError, OSError):
                continue

            if master_fd in rlist:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    break
                os.write(out_fd, data)
                if not accepted:
                    seen_buf = (seen_buf + data)[-SEEN_TAIL_MAX:]
                    cleaned = ANSI_RE.sub(b"", seen_buf).replace(b" ", b"").replace(b"\r", b"").replace(b"\n", b"")
                    if ACCEPT_MARKER in cleaned:
                        os.write(master_fd, b"2\r")
                        accepted = True
                        seen_buf = b""

            if is_tty and in_fd in rlist:
                try:
                    data = os.read(in_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(master_fd, data)
    finally:
        if old_attrs is not None:
            try:
                termios.tcsetattr(in_fd, termios.TCSADRAIN, old_attrs)
            except Exception:
                pass

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
