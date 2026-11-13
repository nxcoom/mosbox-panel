"""进程守护管理器:启动/停止/重启、自动拉活(带退避)、日志轮转与实时广播。

任何 Python/其它语言的程序都能守护:entrypoint 优先,或自定义 command(如 node index.js)。
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_CRASHED = "crashed"

STABLE_UPTIME = 60  # 运行超过该秒数后重置重启退避


class LogWriter:
    """带大小轮转的日志写入器。"""

    def __init__(self, path: Path, max_mb: float = 2, keep: int = 5):
        self.path = Path(path)
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.keep = keep
        self._fh = open(self.path, "ab")

    def write(self, data: bytes) -> None:
        try:
            if self.path.stat().st_size >= self.max_bytes:
                self._rotate()
        except OSError:
            pass
        self._fh.write(data)
        self._fh.flush()

    def _rotate(self) -> None:
        with contextlib.suppress(Exception):
            self._fh.close()
        for i in range(self.keep - 1, 0, -1):
            src = self.path.with_suffix(self.path.suffix + f".{i}")
            dst = self.path.with_suffix(self.path.suffix + f".{i + 1}")
            if src.exists():
                os.replace(src, dst)
        if self.path.exists():
            os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
        self._fh = open(self.path, "ab")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._fh.close()


class WorkloadChannel:
    """单个工作负载的实时输出通道:历史 + 订阅者广播(线程安全入口)。"""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.history: collections.deque[str] = collections.deque(maxlen=1500)
        self.subs: set[asyncio.Queue] = set()

    def broadcast_threadsafe(self, text: str) -> None:
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self._push, text)

    def _push(self, text: str) -> None:
        self.history.append(text)
        dead = []
        for q in list(self.subs):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.subs.discard(q)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        for line in list(self.history):
            q.put_nowait(line)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)


class _Proc:
    """运行中的进程运行时信息。"""

    def __init__(self):
        self.popen: subprocess.Popen | None = None
        self.log: LogWriter | None = None
        self.channel: WorkloadChannel | None = None
        self.manual_stop = False
        self.started_at = 0.0
        self.exit_code: int | None = None
        self.fails = 0


class WorkloadManager:
    def __init__(self, cfg, db, events, dirs_map):
        self.cfg = cfg
        self.db = db
        self.events = events
        self.dirs = dirs_map
        self.loop: asyncio.AbstractEventLoop | None = None
        self._procs: dict[str, _Proc] = {}
        self._restart_tasks: dict[str, asyncio.Task] = {}
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    # ------------------------------------------------------------------
    def workload_dir(self, wid: str) -> Path:
        p = self.dirs["programs"] / wid
        p.mkdir(parents=True, exist_ok=True)
        return p

    def log_path(self, wid: str) -> Path:
        return self.dirs["logs"] / f"wl-{wid}.log"

    def channel(self, wid: str) -> WorkloadChannel:
        with self._lock:
            proc = self._procs.get(wid)
            if proc and proc.channel:
                return proc.channel
        # 离线通道:仅回放日志尾部
        ch = WorkloadChannel(self.loop or asyncio.get_running_loop())
        try:
            tail = self.read_log(wid, lines=300)
            for line in tail:
                ch.history.append(line)
        except Exception:
            pass
        return ch

    def list_all(self) -> list[dict]:
        rows = self.db.list_workloads()
        for r in rows:
            proc = self._procs.get(r["id"])
            if proc and proc.popen and proc.popen.poll() is None:
                r["status"] = STATE_RUNNING if not proc.manual_stop else r["status"]
                r["pid"] = proc.popen.pid
                r["uptime"] = round(time.time() - proc.started_at, 1)
            elif r.get("status") == STATE_RUNNING:
                r["status"] = STATE_STOPPED
        return rows

    def get_state(self, wid: str) -> dict | None:
        for r in self.list_all():
            if r["id"] == wid:
                return r
        return None

    def read_log(self, wid: str, lines: int = 200) -> list[str]:
        path = self.log_path(wid)
        if not path.exists():
            return []
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                start = max(0, size - 512 * 1024)
                f.seek(start)
                data = f.read()
            out = data.decode("utf-8", "replace").splitlines()
            return out[-lines:]
        except OSError:
            return []

    # ------------------------------------------------------------------
    @staticmethod
    def _entry_is_program(path: Path) -> bool:
        """入口文件是可直接执行的程序(ELF 二进制或带执行位的非 Python 文件)。"""
        try:
            if not path.is_file():
                return False
            with open(path, "rb") as f:
                magic = f.read(4)
            if magic.startswith(b"\x7fELF"):       # Linux 可执行
                return True
            if os.access(path, os.X_OK) and not path.name.endswith(".py"):
                return True                          # 有执行位(含带 shebang 的脚本)
            return False
        except OSError:
            return False

    def _build_argv(self, w: dict) -> list[str]:
        command = (w.get("command") or "").strip()
        if command:
            argv = shlex.split(command)
            # "python"/"python3" 统一替换为面板当前解释器(容器环境友好)
            if argv and argv[0] in ("python", "python3"):
                argv[0] = sys.executable
            return argv
        wdir = self.workload_dir(w["id"])
        entry = (w.get("entrypoint") or "main.py").strip()
        entry_path = wdir / entry
        if self._entry_is_program(entry_path):
            return [str(entry_path)]                 # 二进制/可执行脚本直接运行
        return [sys.executable, "-u", entry]

    def _build_env(self, w: dict) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["BKP_WORKLOAD_ID"] = w["id"]
        env["BKP_WORKLOAD_NAME"] = w["name"]
        env["PATH"] = str(self.dirs["bin"]) + os.pathsep + env.get("PATH", "")
        for k, v in (w.get("env") or {}).items():
            if v is None:
                env.pop(str(k), None)
            else:
                env[str(k)] = str(v)
        return env

    async def install_deps(self, wid: str, force: bool = False) -> bool:
        """安装 requirements.txt(存在才装);成功后写 .botkeep_deps_ok 标记。"""
        w = self.db.get_workload(wid)
        if not w:
            return False
        wdir = self.workload_dir(wid)
        req = wdir / "requirements.txt"
        if not req.exists():
            return False
        marker = wdir / ".botkeep_deps_ok"
        if marker.exists() and not force:
            return True
        ch = self._ensure_channel(wid)
        ch.broadcast_threadsafe(f"[botkeep] 检测到 requirements.txt,正在安装依赖…\n")
        argv = [sys.executable, "-m", "pip", "install", "--no-warn-script-location",
                "--disable-pip-version-check", "-q", "-r", "requirements.txt"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(wdir), env=self._build_env(w),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            ch.broadcast_threadsafe(f"[botkeep] 依赖安装启动失败: {e}\n")
            return False
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            ch.broadcast_threadsafe(line.decode("utf-8", "replace"))
        code = await proc.wait()
        if code == 0:
            marker.write_text("ok\n")
            ch.broadcast_threadsafe("[botkeep] 依赖安装完成 ✓\n")
            return True
        marker.unlink(missing_ok=True)
        ch.broadcast_threadsafe(f"[botkeep] 依赖安装失败(exit={code}),可稍后手动重试\n")
        return False

    def _ensure_proc(self, wid: str) -> _Proc:
        """获取(或创建)工作负载的运行时对象。"""
        with self._lock:
            proc = self._procs.get(wid)
            if proc is None:
                proc = _Proc()
                self._procs[wid] = proc
            if proc.channel is None:
                proc.channel = WorkloadChannel(self.loop or asyncio.get_running_loop())
            return proc

    def _ensure_channel(self, wid: str) -> WorkloadChannel:
        return self._ensure_proc(wid).channel

    async def start_workload(self, wid: str) -> dict:
        w = self.db.get_workload(wid)
        if not w:
            raise KeyError(wid)
        proc = self._ensure_proc(wid)
        if proc.popen and proc.popen.poll() is None:
            return {"ok": False, "reason": "已在运行"}
        await self.install_deps(wid)

        w = self.db.get_workload(wid) or w
        argv = self._build_argv(w)
        ch = proc.channel
        wdir = self.workload_dir(wid)
        self.db.update_workload(wid, status=STATE_STARTING)
        ch.broadcast_threadsafe(f"[botkeep] ▶ 启动 {' '.join(argv)} (cwd={wdir})\n")

        log = LogWriter(self.log_path(wid), self.cfg.get("supervisor", {}).get("log_rotate_mb", 2),
                        self.cfg.get("supervisor", {}).get("log_keep", 5))
        try:
            popen = subprocess.Popen(
                argv, cwd=str(wdir), env=self._build_env(w),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                bufsize=0, start_new_session=True,
            )
        except OSError as e:
            log.close()
            self.db.update_workload(wid, status=STATE_CRASHED, last_exit=-1)
            ch.broadcast_threadsafe(f"[botkeep] ✗ 启动失败: {e}\n")
            self.events.emit("workload.error", f"{w['name']} 启动失败: {e}")
            return {"ok": False, "reason": str(e)}

        proc.popen = popen
        proc.log = log
        proc.manual_stop = False
        proc.started_at = time.time()
        proc.exit_code = None
        self.db.update_workload(wid, status=STATE_RUNNING, pid=popen.pid,
                                last_started=proc.started_at, last_exit=None)
        self.events.emit("workload.start", f"{w['name']} 已启动 (pid {popen.pid})",
                         {"id": wid, "pid": popen.pid})
        threading.Thread(target=self._pump, args=(wid, popen), daemon=True).start()
        threading.Thread(target=self._watch, args=(wid, popen), daemon=True).start()
        return {"ok": True, "pid": popen.pid}

    def _pump(self, wid: str, popen: subprocess.Popen) -> None:
        proc = self._procs.get(wid)
        if not proc or not proc.log or not proc.channel:
            return
        try:
            for raw in iter(popen.stdout.readline, b""):
                if not raw:
                    break
                proc.log.write(raw)
                proc.channel.broadcast_threadsafe(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass

    def _watch(self, wid: str, popen: subprocess.Popen) -> None:
        code: int | None = None
        try:
            code = popen.wait()
        except Exception:
            code = popen.poll()
        proc = self._procs.get(wid)
        if not proc:
            return
        if proc.log:
            proc.log.close()
        ran = time.time() - proc.started_at
        w = self.db.get_workload(wid) or {}
        name = w.get("name", wid)
        manual = proc.manual_stop
        if manual:
            status = STATE_STOPPED
            self.events.emit("workload.stop", f"{name} 已停止 (exit={code})",
                             {"id": wid, "exit": code})
            proc.fails = 0
        elif code is not None and code <= 128:
            status = STATE_CRASHED
            proc.fails = 0 if ran > STABLE_UPTIME else proc.fails + 1
            self.events.emit("workload.crash",
                             f"{name} 已退出 (exit={code}, 运行 {int(ran)}s)",
                             {"id": wid, "exit": code})
        else:  # 被信号杀死且非手动
            status = STATE_CRASHED
            proc.fails = 0 if ran > STABLE_UPTIME else proc.fails + 1
            self.events.emit("workload.crash", f"{name} 被信号终止 (signal={code})",
                             {"id": wid, "exit": code})
        self.db.update_workload(wid, status=status, pid=None, last_exit=code)
        ch = proc.channel
        if ch:
            emoji = "■" if manual else "✗"
            ch.broadcast_threadsafe(f"[botkeep] {emoji} 进程退出 (exit={code})\n")

        # 自动拉活
        if not manual and status == STATE_CRASHED and w.get("auto_restart") \
                and self.loop and self._restart_tasks.get(wid) is None:
            backoff = min(2 ** max(proc.fails - 1, 0),
                          int(self.cfg.get("supervisor", {}).get("backoff_max", 30)))
            if ch:
                ch.broadcast_threadsafe(
                    f"[botkeep] ⟳ {backoff}s 后自动重启(连续失败 {proc.fails})\n")
            self._restart_tasks[wid] = self.loop.create_task(self._auto_restart(wid, backoff))

    async def _auto_restart(self, wid: str, backoff: float) -> None:
        try:
            await asyncio.sleep(backoff)
            self._restart_tasks.pop(wid, None)
            w = self.db.get_workload(wid)
            if not w or w.get("status") != STATE_CRASHED:
                return
            proc = self._procs.get(wid)
            if proc and proc.popen and proc.popen.poll() is None:
                return
            self.db.update_workload(wid, restarts=(w.get("restarts") or 0) + 1)
            await self.start_workload(wid)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.events.emit("workload.error", f"自动重启失败 {wid}: {e}")
            self._restart_tasks.pop(wid, None)

    async def stop_workload(self, wid: str, timeout: float = 6.0) -> dict:
        task = self._restart_tasks.pop(wid, None)
        if task:
            task.cancel()
        proc = self._procs.get(wid)
        w = self.db.get_workload(wid) or {}
        name = w.get("name", wid)
        if not proc or not proc.popen or proc.popen.poll() is not None:
            self.db.update_workload(wid, status=STATE_STOPPED, pid=None)
            return {"ok": True, "reason": "未在运行"}
        proc.manual_stop = True
        try:
            os.killpg(os.getpgid(proc.popen.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(Exception):
                proc.popen.terminate()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.popen.poll() is not None:
                break
            await asyncio.sleep(0.15)
        if proc.popen.poll() is None:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(proc.popen.pid), signal.SIGKILL)
            self.events.emit("workload.stop", f"{name} 强制终止 (SIGKILL)", {"id": wid})
        return {"ok": True}

    async def restart_workload(self, wid: str) -> dict:
        await self.stop_workload(wid)
        return await self.start_workload(wid)

    def write_stdin(self, wid: str, data: str) -> bool:
        proc = self._procs.get(wid)
        if proc and proc.popen and proc.popen.poll() is None and proc.popen.stdin:
            try:
                proc.popen.stdin.write(data.encode("utf-8"))
                proc.popen.stdin.flush()
                return True
            except (OSError, ValueError):
                return False
        return False

    async def startup_autostart(self) -> None:
        for w in self.db.list_workloads():
            if w.get("autostart"):
                with contextlib.suppress(Exception):
                    await self.start_workload(w["id"])

    async def shutdown_all(self) -> None:
        for task in self._restart_tasks.values():
            task.cancel()
        self._restart_tasks.clear()
        for wid, proc in list(self._procs.items()):
            if proc.popen and proc.popen.poll() is None:
                proc.manual_stop = True
                with contextlib.suppress(Exception):
                    os.killpg(os.getpgid(proc.popen.pid), signal.SIGTERM)
        await asyncio.sleep(0.5)
        for wid, proc in list(self._procs.items()):
            if proc.popen and proc.popen.poll() is None:
                with contextlib.suppress(Exception):
                    os.killpg(os.getpgid(proc.popen.pid), signal.SIGKILL)

    # ------------------------------------------------------------------
    def export_zip(self, wid: str) -> Path | None:
        w = self.db.get_workload(wid)
        wdir = self.workload_dir(wid)
        out = self.dirs["run"] / f"export-{wid}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(wdir.rglob("*")):
                if path.is_file() and ".botkeep_deps_ok" not in path.name:
                    zf.write(path, path.relative_to(wdir).as_posix())
        return out

    def import_zip(self, wid: str, zpath: Path) -> None:
        wdir = self.workload_dir(wid)
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                member = info.filename
                if member.startswith("__MACOSX") or ".." in Path(member).parts:
                    continue
                zf.extract(info, wdir)
                # zipfile 不保留 unix 权限 —— 从 external_attr 恢复执行位,
                # 二进制程序才能直接运行
                mode = (info.external_attr >> 16) & 0o777
                if mode & 0o111:
                    with contextlib.suppress(OSError):
                        (wdir / member).chmod(0o755)
        # 兜底:zip 里没记录权限时,按 ELF 魔数补执行位
        for path in wdir.rglob("*"):
            if path.is_file() and not os.access(path, os.X_OK):
                with contextlib.suppress(OSError):
                    with open(path, "rb") as f:
                        if f.read(4).startswith(b"\x7fELF"):
                            path.chmod(0o755)
        (wdir / ".botkeep_deps_ok").unlink(missing_ok=True)
