#!/usr/bin/env python3
"""Start Codex under a PTY and auto-accept local trust prompts.

The research_mvp runtime launches one TUI per tmux window. Codex may ask
whether the current repository is trusted before loading local config. This
wrapper answers that prompt so the runtime can start unattended.
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


CHILD_ARGV = [
    "codex",
    "-m",
    os.environ.get("TINYKAGGLE_CODEX_MODEL", "gpt-5.4"),
    "--dangerously-bypass-approvals-and-sandbox",
    *sys.argv[1:],
]
ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]")
TRUST_MARKERS = (
    b"Doyoutrustthecontentsofthisdirectory",
    b"1.Yes,continue",
)


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

    accepted_trust = False
    seen_buf = b""
    tail_max = 8192

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
                if not accepted_trust:
                    seen_buf = (seen_buf + data)[-tail_max:]
                    cleaned = ANSI_RE.sub(b"", seen_buf)
                    cleaned = cleaned.replace(b" ", b"").replace(b"\r", b"").replace(b"\n", b"")
                    if all(marker in cleaned for marker in TRUST_MARKERS):
                        os.write(master_fd, b"1\r")
                        accepted_trust = True
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
