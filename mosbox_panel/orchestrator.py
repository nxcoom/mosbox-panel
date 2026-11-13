"""编排器:装配面板的三个常驻进程并在后台守护它们。

进程结构(2.0 重写 —— 不再手写协议嗅探与 SSH 服务):

    公网单端口 ──▶ raddy(L4 分流,识别 SSH banner)
                    ├── SSH      ──▶ puressh sshd(纯 Rust,公钥认证)
                    └── 其余流量 ──▶ raddy HTTP
                                     ├── 静态前端文件
                                     └── /api/* /p/* ──▶ 本面板 FastAPI(uvicorn)

三个子进程(raddy / sshd / uvicorn)由本模块拉起;若子进程意外退出,
带退避自动拉活。所有内部端口启动时动态挑选,避免端口冲突。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from . import APP_NAME, APP_VERSION
from .auth import Auth
from .config import Config, dirs, ensure_initial_password
from .ctl import write_botkeepctl
from .database import Database
from .events import EventBus
from .r2store import R2Store
from .ssh import SshManager
from .stats import StatsCollector
from .supervisor import WorkloadManager
from .webapp import make_app

BANNER_ART = r"""
     __  __             ____  _         _
    |  \/  |_   _ ___  | __ )| | ____ _| |_ ___  ___
    | |\/| | | | / __| |  _ \| |/ / _` | __/ _ \/ __|
    | |  | | |_| \__ \ | |_) |   < (_| | || (_) \__ \
    |_|  |_|\__,_|___/ |____/|_|\_\__,_|\__\___/|___/
   raddy(L4 分流) + puressh sshd + FastAPI · 单端口复用
"""


class Ctx:
    """全局共享上下文(传给 webapp)。"""

    def __init__(self, cfg, db, events, auth, supervisor, stats, r2store, ssh, dirs_map):
        self.cfg = cfg
        self.db = db
        self.events = events
        self.auth = auth
        self.supervisor = supervisor
        self.stats = stats
        self.r2store = r2store
        self.ssh = ssh
        self.dirs = dirs_map
        self.ports = {}       # internal ports, 供展示/诊断
        self.panel_url = ""   # 本机直连面板 API 的地址


def _free_port() -> int:
    """向内核要一个空闲回环端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        time.sleep(0.15)
    return False


# ----------------------------------------------------------------------
# 配置文件渲染
# ----------------------------------------------------------------------
def render_caddyfile(path: Path, public_port: int,
                     api_port: int, sshd_port: int) -> Path:
    text = f"""\
# MosBox Panel —— 由编排器自动生成,请勿手工编辑(重启后会重新生成)
# 单端口复用:SSH banner ──▶ puressh sshd;其余(HTTP/WS)──▶ 面板 uvicorn。
# HTTP 全部交给 uvicorn 原生处理(gzip/安全头由 FastAPI 中间件负责),
# 避免 raddy HTTP 引擎对公网分段请求体的兼容性问题。
{{
    admin off
    auto_https off
}}

layer4 {{
    :{public_port} {{
        @ssh ssh
        route @ssh {{
            proxy 127.0.0.1:{sshd_port}
        }}
        route {{
            proxy 127.0.0.1:{api_port}
        }}
    }}
}}
"""
    path.write_text(text, encoding="utf-8")
    return path


def render_sshd_config(path: Path, sshd_port: int, host_key: Path,
                       pid_file: Path, panel_url: str, token: str,
                       data_root: Path, ssh_user: str = "root") -> Path:
    text = f"""\
# MosBox Panel —— puressh sshd 配置(编排器自动生成)
Port {sshd_port}
ListenAddress 127.0.0.1
HostKey {host_key}
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
AllowUsers {ssh_user}
AllowTcpForwarding yes
AllowAgentForwarding no
X11Forwarding no
GatewayPorts no
Subsystem sftp internal-sftp
PidFile {pid_file}
LogLevel INFO
PrintMotd no
SetEnv BKP_PANEL={panel_url} BKP_TOKEN={token} BKP_DATA={data_root}
"""
    path.write_text(text, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 子进程守护
# ----------------------------------------------------------------------
class Child:
    """一个受管子进程 + 自动拉活。"""

    def __init__(self, name: str, argv: list[str], log_path: Path,
                 env: dict | None = None, max_retries: int = 5,
                 kill_group: bool = True):
        self.name = name
        self.argv = argv
        self.log_path = log_path
        self.env = env
        self.max_retries = max_retries
        self.kill_group = kill_group    # sshd 需要只杀守护进程,保留活动会话
        self.proc: subprocess.Popen | None = None
        self.fails = 0

    def start(self) -> None:
        log = open(self.log_path, "ab", buffering=0)
        try:
            log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 {' '.join(self.argv)}\n".encode())
            try:
                self.proc = subprocess.Popen(
                    self.argv, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, env=self.env,
                    start_new_session=True, close_fds=True)
            except OSError as e:
                # 二进制缺失/不可执行等:控制台 + 日志双写,保持 proc=None
                msg = f"[botkeep] ✗ {self.name} 启动失败: {e} (argv0={self.argv[0]})"
                print(msg, flush=True)
                log.write(f"{msg}\n".encode())
                self.proc = None
        finally:
            log.close()

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _signal(self, sig: int) -> None:
        """发送信号;kill_group=False 时只杀守护进程本身(不波及 SSH 会话子进程)。"""
        with contextlib.suppress(Exception):
            if self.kill_group:
                os.killpg(os.getpgid(self.proc.pid), sig)
            else:
                os.kill(self.proc.pid, sig)

    def stop(self) -> None:
        if not self.alive:
            return
        self._signal(signal.SIGTERM)
        deadline = time.time() + 5
        while time.time() < deadline and self.alive:
            time.sleep(0.1)
        if self.alive:
            self._signal(signal.SIGKILL)

    def restart(self) -> None:
        self.stop()
        self.start()


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
async def run() -> None:
    app_root = Path(__file__).resolve().parent.parent
    bin_dir = Path(os.environ.get("BKP_BIN_DIR") or (app_root / "bin"))
    data_dir = Path(os.environ.get("BOTKEEP_DATA") or app_root / "data")
    dm = dirs(data_dir)
    cfg = Config(data_dir)   # 公网端口:BOTKEEP_PORT/PORT/SERVER_PORT 环境变量或默认 8800

    first_pw = ensure_initial_password(cfg)
    db = Database(data_dir / "panel.db")
    events = EventBus(db)
    auth = Auth(cfg)
    sup = WorkloadManager(cfg, db, events, dm)
    stats = StatsCollector(sup, dm)
    r2 = R2Store(cfg, db, events)
    ssh = SshManager(cfg, db, dm, bin_dir)

    # 本机回环令牌(botkeepctl → 面板 API)
    token_file = dm["run"] / "local_token"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(token_file, 0o600)
    token = token_file.read_text(encoding="utf-8").strip()

    # ---- SSH 准备:主机密钥 / 默认公钥 / 授权文件 ----
    # 登录名统一用 root(用户习惯);容器以非 root uid 运行时,puressh 补丁
    # 会把 root 映射到进程自身身份(会话即应用身份,免 setuid)。
    ssh_user = "root"
    ssh.user = ssh_user

    ssh_ready = True
    try:
        ssh.ensure_host_key()
        ssh.seed_default_key()
        ssh.render_authorized_keys()
    except Exception as e:
        ssh_ready = False
        print(f"[botkeep] ✗ SSH 准备失败(面板仍会启动,SSH 不可用): {e}", flush=True)

    # ---- 内部端口 ----
    api_port, sshd_port = _free_port(), _free_port()
    ctx = Ctx(cfg, db, events, auth, sup, stats, r2, ssh, dm)
    ctx.ports = {"api": api_port, "sshd": sshd_port}
    ctx.panel_url = f"http://127.0.0.1:{api_port}"

    # ---- botkeepctl 写入(SSH 会话里直接可用) ----
    with contextlib.suppress(Exception):
        write_botkeepctl(dm["bin"], ctx.panel_url, token_file)
        target = Path("/usr/local/bin/botkeepctl")
        target.write_text((dm["bin"] / "botkeepctl").read_text(encoding="utf-8"))
        target.chmod(0o755)
    # SSH 会话 shell 包装器:注入 PATH 后转发到 bash,让交互/命令两种模式
    # 都能直接用 botkeepctl(合成用户无 passwd,不能依赖 /etc/profile)
    with contextlib.suppress(Exception):
        shell_path = dm["bin"] / "mosbox-shell"
        shell_path.write_text(
            "#!/bin/bash\n"
            "# MosBox Panel 会话 shell:补 PATH 后转交 bash\n"
            'export PATH="$HOME/data/bin:$PATH"\n'
            'exec /bin/bash "$@"\n',
            encoding="utf-8")
        shell_path.chmod(0o755)

    # ---- 渲染配置文件 ----
    run_dir = dm["run"]
    caddyfile = render_caddyfile(
        run_dir / "Caddyfile", cfg.port, api_port, sshd_port)
    sshd_config = render_sshd_config(
        run_dir / "sshd_config", sshd_port, ssh.host_key,
        run_dir / "sshd.pid", ctx.panel_url, token, dm["root"], ssh_user)

    # ---- 拉起子进程 ----
    env = os.environ.copy()
    # sshd 会话的 HOME 锚定到应用根目录(合成用户的 home 从环境变量读取)
    if env.get("HOME") != str(app_root):
        env["BKP_REAL_HOME"] = env.get("HOME", "")
        env["HOME"] = str(app_root)
    env["BKP_SHELL"] = str(dm["bin"] / "mosbox-shell")
    children: dict[str, Child] = {}
    if ssh_ready:
        sshd = Child("sshd",
                     [str(bin_dir / "sshd"), "-D", "-f", str(sshd_config),
                      "-A", str(ssh.authorized_keys), "-u", ssh_user,
                      "--no-strict-modes", "-E", str(dm["logs"] / "sshd.log")],
                     dm["logs"] / "sshd-boot.log", env, kill_group=False)
        sshd.start()
        if _wait_port(sshd_port, 10):
            print(f"[botkeep] ✓ SSH 服务就绪 (puressh sshd :{sshd_port})", flush=True)
        else:
            print("[botkeep] ✗ sshd 端口未就绪,请查看 data/logs/sshd-boot.log", flush=True)
        ssh.proc = sshd.proc
        children["sshd"] = sshd
    else:
        print("[botkeep] SSH 已禁用本次启动", flush=True)

    raddy_bin = bin_dir / "raddy"
    if raddy_bin.exists():
        head = raddy_bin.read_bytes()[:8]
        print(f"[botkeep] raddy 二进制: {raddy_bin} {raddy_bin.stat().st_size} 字节 "
              f"magic={head.hex()}", flush=True)
    else:
        print(f"[botkeep] ✗ raddy 二进制不存在: {raddy_bin}", flush=True)
    raddy = Child("raddy",
                  [str(raddy_bin), "run", "--config", str(caddyfile)],
                  dm["logs"] / "raddy-boot.log", env)
    raddy.start()
    children["raddy"] = raddy

    if not _wait_port(cfg.port, 15):
        print("[botkeep] ✗ 公网端口未就绪,请查看 data/logs/raddy.log", flush=True)

    # ---- 本面板 API(uvicorn,进程内) ----
    loop = asyncio.get_running_loop()
    events.bind_loop(loop)
    sup.bind_loop(loop)

    uconfig = uvicorn.Config(
        make_app(ctx), host="127.0.0.1", port=api_port, log_level="warning",
        lifespan="off", access_log=False, ws_max_size=16 * 1024 * 1024)
    server = uvicorn.Server(uconfig)
    if not server.config.loaded:
        server.config.load()
    server.lifespan = uconfig.lifespan_class(uconfig)
    uvicorn_task = loop.create_task(server.serve())
    await asyncio.sleep(0.3)

    if not _wait_port(api_port, 10):
        print("[botkeep] ✗ 面板 API 未就绪", flush=True)

    # ---- 统计 + 自动拉活子进程 ----
    await stats.start()

    async def watch_children():
        backoff = 2
        reported_dead: set[str] = set()
        while True:
            await asyncio.sleep(3)
            for name, ch in children.items():
                if ch.alive:
                    ch.fails = 0
                    reported_dead.discard(name)
                    continue
                ch.fails += 1
                if ch.fails > ch.max_retries:
                    if name not in reported_dead:
                        reported_dead.add(name)
                        events.emit("service.died", f"{name} 反复退出,暂停自动拉活",
                                    {"name": name})
                    continue
                events.emit("service.restart",
                            f"{name} 意外退出(exit={ch.proc.returncode if ch.proc else '?'}),"
                            f"{backoff}s 后拉活", {"name": name})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                try:
                    ch.restart()
                    if name == "sshd":
                        ssh.proc = ch.proc
                    print(f"[botkeep] ⟳ {name} 已重新拉起", flush=True)
                except Exception as e:
                    events.emit("service.error", f"{name} 拉活失败: {e}", {"name": name})

    watcher = asyncio.create_task(watch_children())

    # ---- sshd 干净重启回调(公钥变更时由 SshManager 触发) ----
    if ssh_ready and children.get("sshd"):
        def _restart_sshd():
            ch = children["sshd"]
            ch.restart()
            ssh.proc = ch.proc
            events.emit("service.restart", "SSH 服务已重启以应用新的公钥列表", {"name": "sshd"})
        ssh.restart_cb = _restart_sshd

    # ---- 开机自启的工作负载 ----
    await sup.startup_autostart()

    # ---- 欢迎信息 ----
    print(BANNER_ART, flush=True)
    print(f"[botkeep] {APP_NAME} v{APP_VERSION}", flush=True)
    print(f"[botkeep] 公网端口 : {cfg.bind}:{cfg.port}  (Web 面板 + SSH 复用)", flush=True)
    print(f"[botkeep] 打开面板 : http://<你的IP>:{cfg.port}/", flush=True)
    if ssh_ready:
        print(f"[botkeep] SSH 连接   : ssh -p {cfg.port} root@<你的IP>", flush=True)
        print(f"[botkeep] 主机密钥   : {ssh.host_key_fp}", flush=True)
    if first_pw:
        print(f"[botkeep] 初始密码   : {first_pw}  (已写入 data/initial_password.txt,"
              f"首次登录后自动删除)", flush=True)
    print(f"[botkeep] 内部端口   : api={api_port} sshd={sshd_port}", flush=True)
    sys.stdout.flush()

    # ---- 信号处理:优雅退出 ----
    stop_event = asyncio.Event()
    def _sig(*_):
        stop_event.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(Exception):
            loop.add_signal_handler(s, _sig)

    await stop_event.wait()
    print("\n[botkeep] 正在退出…", flush=True)
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher
    await sup.shutdown_all()
    await stats.stop()
    for ch in children.values():
        ch.stop()
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(uvicorn_task, timeout=5)
    print("[botkeep] 已退出。", flush=True)
