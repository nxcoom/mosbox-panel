#!/usr/bin/env python3
"""MosBox Panel 入口 —— 自举:

1. 加载 .env(botkeep 等平台把环境变量写在这里);
2. 项目 .venv 优先(存在且当前解释器不在其中时自动切换);
3. Python 依赖缺失时自动安装后重启自身;
4. bin/ 里缺 raddy / puressh sshd 二进制时,从 GitHub Releases 下载
   (botkeep 容器只给单文件上传,二进制走首次启动下载)。
"""
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))

BIN_DIR = APP_ROOT / "bin"

# 固定版本,保证可复现;可用环境变量覆盖
RADDY_VERSION = os.environ.get("BKP_RADDY_VERSION", "v0.10.0")
PURESSH_VERSION = os.environ.get("BKP_PURESSH_VERSION", "v0.2.9")
RADDY_URL = os.environ.get(
    "BKP_RADDY_URL",
    f"https://github.com/fokx/raddy/releases/download/{RADDY_VERSION}/"
    "raddy-x86_64-unknown-linux-gnu.tar.gz")
PURESSH_URL = os.environ.get(
    "BKP_PURESSH_URL",
    f"https://github.com/KarpelesLab/puressh/releases/download/{PURESSH_VERSION}/"
    "puressh-v" + PURESSH_VERSION.lstrip("v") + "-linux-x86_64.tar.gz")


def _deps_ok() -> bool:
    for m in ("fastapi", "uvicorn", "boto3", "psutil", "multipart", "httpx"):
        try:
            __import__(m)
        except ImportError:
            return False
    return True


def _load_dotenv() -> None:
    """加载同目录 .env(botkeep 等平台把环境变量写在这里)。"""
    env_file = APP_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _exec_python(python_bin: str) -> None:
    os.execv(python_bin, [python_bin, str(APP_ROOT / "main.py")])


def _bootstrap_deps() -> None:
    print("[botkeep] 首次启动:正在安装依赖…", flush=True)
    req = str(APP_ROOT / "requirements.txt")
    base = [sys.executable, "-m", "pip", "install",
            "--no-warn-script-location", "--disable-pip-version-check", "-q"]
    # 依次尝试:普通安装 → --user → 突破 PEP 668
    for extra in ([], ["--user"], ["--break-system-packages"]):
        code = subprocess.call(base + extra + ["-r", req])
        if code == 0:
            return
    print("[botkeep] 依赖安装失败,请手动执行: pip install -r requirements.txt", flush=True)
    sys.exit(1)


def _extract_binary(tar_path: Path, name: str, dest: Path) -> bool:
    """从 tar.gz 里找出名字匹配的文件并复制到 dest。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and Path(member.name).name == name:
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    data = src.read()
                    # 防路径穿越:显式校验成员路径
                    if member.name.startswith("/") or ".." in Path(member.name).parts:
                        continue
                    dest.write_bytes(data)
                    dest.chmod(0o755)
                    return True
    except (tarfile.TarError, OSError):
        return False
    return False


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MosBox-Panel/2.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        return True
    except Exception as e:
        print(f"[botkeep] 下载失败 {url}: {e}", flush=True)
        return False


def ensure_binaries() -> None:
    """bin/ 缺二进制时从 GitHub Releases 下载。"""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    missing = []
    if not (BIN_DIR / "raddy").exists():
        missing.append("raddy")
    if not (BIN_DIR / "sshd").exists():
        missing.append("sshd")
    if not missing:
        return
    print(f"[botkeep] 缺少二进制 {missing},开始从 GitHub Releases 下载…", flush=True)
    jobs = []
    if "raddy" in missing:
        jobs.append((RADDY_URL, [("raddy", BIN_DIR / "raddy")]))
    if "sshd" in missing:
        jobs.append((PURESSH_URL, [("sshd", BIN_DIR / "sshd"),
                                   ("ssh-keygen", BIN_DIR / "ssh-keygen")]))
    for url, targets in jobs:
        with tempfile.TemporaryDirectory(prefix="bkdl-") as td:
            tgz = Path(td) / "pkg.tar.gz"
            if not _download(url, tgz):
                continue
            for name, dest in targets:
                if dest.exists():
                    continue
                if _extract_binary(tgz, name, dest):
                    print(f"[botkeep] ✓ 已安装 {name} ({dest.stat().st_size // 1024} KB)",
                          flush=True)
                else:
                    print(f"[botkeep] ✗ 压缩包里没有 {name}", flush=True)


if __name__ == "__main__":
    _load_dotenv()
    # 项目 .venv 优先(仅自举一次,避免循环 exec)
    venv_python = APP_ROOT / ".venv" / "bin" / "python3"
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if venv_python.exists() and not in_venv:
        _exec_python(str(venv_python))
    if not _deps_ok():
        _bootstrap_deps()
        os.execv(sys.executable, [sys.executable, str(APP_ROOT / "main.py")])
    ensure_binaries()
    import asyncio
    from mosbox_panel.orchestrator import run
    asyncio.run(run())
