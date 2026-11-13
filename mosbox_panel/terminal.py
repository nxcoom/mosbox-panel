"""Web 终端:真实 PTY 中的登录 shell(与 SSH 会话同一环境)。"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
from pathlib import Path


class PTYSession:
    """真实 PTY 中的 shell。"""

    def __init__(self, shell_argv: list[str], cwd: str, env: dict,
                 on_output, on_exit):
        self.master_fd, slave_fd = pty.openpty()
        self.proc = subprocess.Popen(
            shell_argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=cwd, env=env, start_new_session=True, close_fds=True,
        )
        os.close(slave_fd)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL,
                    fcntl.fcntl(self.master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        self._on_output = on_output
        self._on_exit = on_exit
        self._loop = asyncio.get_running_loop()
        self._closed = False

    def attach_reader(self) -> None:
        self._loop.add_reader(self.master_fd, self._readable)

    def _readable(self) -> None:
        try:
            data = os.read(self.master_fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self.close()
            return
        if not data:
            self.close()
            return
        self._on_output(data)

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            os.write(self.master_fd, data)
        except OSError:
            self.close()

    def resize(self, rows: int, cols: int) -> None:
        try:
            winsize = struct.pack("HHHH", max(rows, 1), max(cols, 1), 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._loop.remove_reader(self.master_fd)
        with contextlib.suppress(Exception):
            if self.proc.poll() is None:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        with contextlib.suppress(Exception):
            os.close(self.master_fd)
        self._on_exit()


def pick_shell() -> str:
    for cand in ("/bin/bash", "/usr/bin/bash", "/bin/sh"):
        if os.path.exists(cand):
            return cand
    return "/bin/sh"


def session_env(dirs_map: dict, panel_url: str, token_file: Path) -> dict:
    """Web 终端与 SSH 会话共用的环境变量(botkeepctl 直连面板用)。"""
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["PATH"] = str(dirs_map["bin"]) + os.pathsep + env.get("PATH", "")
    env["BKP_PANEL"] = panel_url
    env["BKP_DATA"] = str(dirs_map["root"])
    if token_file.exists():
        env["BKP_TOKEN"] = token_file.read_text(encoding="utf-8").strip()
    env.pop("SSH_CLIENT", None)
    env.pop("SSH_CONNECTION", None)
    return env
