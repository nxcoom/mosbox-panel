"""SSH 服务管理:主机密钥、authorized_keys 渲染、puressh sshd 进程控制。

真正的 SSH 协议由 puressh 的 sshd 二进制(bin/sshd)提供 —— 纯 Rust 实现,
支持 shell / exec / SFTP / SCP / 端口转发。本模块只负责:
  1. 生成/加载 ed25519 主机密钥并计算指纹;
  2. 把数据库中的公钥渲染成 sshd 的全局 authorized_keys 文件(-A);
  3. 对 sshd 进程做 reload(SIGHUP)/存活检查。
"""
from __future__ import annotations

import base64
import hashlib
import os
import signal
import subprocess
from pathlib import Path

from .config import DEFAULT_KEY_NAME, DEFAULT_PUBLIC_KEY

# OpenSSH 公钥 blob 里允许出现的密钥类型
KEY_TYPES = {
    "ssh-ed25519", "ssh-rsa", "ssh-dss", "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
}


def parse_public_key(line: str) -> tuple[str, str] | None:
    """解析一行 OpenSSH 公钥,返回 (类型, SHA256 指纹)。无效返回 None。

    实现:wire 格式就是 "类型字符串 + 一串 length-prefixed 字段",
    对整个 blob 做 SHA256 即 OpenSSH 的 SHA256:xxx 指纹。
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    ktype, blob_b64 = parts[0], parts[1]
    if ktype not in KEY_TYPES:
        return None
    try:
        blob = base64.b64decode(blob_b64, validate=True)
    except Exception:
        return None
    # wire 校验:依次读 length-prefixed 字符串,第一段必须是类型名
    try:
        first_len = int.from_bytes(blob[0:4], "big")
        first = blob[4:4 + first_len]
        if first_len <= 0 or first.decode("ascii") != ktype:
            return None
        # 从第一个字段之后继续校验各字段长度自洽
        pos = 4 + first_len
        while pos < len(blob):
            if pos + 4 > len(blob):
                return None
            n = int.from_bytes(blob[pos:pos + 4], "big")
            if pos + 4 + n > len(blob):
                return None
            pos += 4 + n
        if pos != len(blob):
            return None
    except Exception:
        return None
    fp = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return ktype, f"SHA256:{fp}"


def fingerprint_host_key(path: Path) -> str:
    """计算主机公钥的 SHA256 指纹(与 ssh-keygen -lf 输出一致)。"""
    try:
        for line in Path(path).with_suffix(".pub").read_text().splitlines():
            parsed = parse_public_key(line)
            if parsed:
                return parsed[1]
    except OSError:
        pass
    return "未知"


class SshManager:
    """sshd 的配置面(密钥文件 + 进程句柄由 orchestrator 注入)。"""

    def __init__(self, cfg, db, dirs_map, bin_dir: Path):
        self.cfg = cfg
        self.db = db
        self.dirs = dirs_map
        self.bin_dir = Path(bin_dir)          # 面板自带二进制(raddy/sshd/ssh-keygen)
        self.proc: subprocess.Popen | None = None   # orchestrator 注入
        self.restart_cb = None                 # orchestrator 注入:重启 sshd 的回调
        self.ssh_dir = Path(dirs_map["ssh"])
        self.host_key = self.ssh_dir / "ssh_host_ed25519_key"
        self.authorized_keys = self.ssh_dir / "authorized_keys"

    # ---------------- 密钥 ----------------
    def ensure_host_key(self) -> None:
        if self.host_key.exists():
            return
        keygen = self.bin_dir / "ssh-keygen"
        if not keygen.exists():
            keygen = "ssh-keygen"
        try:
            subprocess.run(
                [str(keygen), "-t", "ed25519", "-f", str(self.host_key), "-N", "", "-q"],
                check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            # keygen 不可用或失败时,用内嵌密钥兜底
            # (botkeep 容器首启下载不到 ssh-keygen,导致 SSH 一直起不来)
            from .hostkey_blob import write_host_key
            if not write_host_key(self.host_key):
                raise
        try:
            os.chmod(self.host_key, 0o600)
        except OSError:
            pass

    @property
    def host_key_fp(self) -> str:
        return fingerprint_host_key(self.host_key)

    def seed_default_key(self) -> None:
        """首次启动把默认公钥写入数据库;指纹缺失时补算。"""
        keys = self.db.list_ssh_keys()
        parsed = parse_public_key(DEFAULT_PUBLIC_KEY)
        fp = parsed[1] if parsed else "SHA256:unknown"
        if not keys:
            self.db.add_ssh_key("key-default", DEFAULT_KEY_NAME, DEFAULT_PUBLIC_KEY, fp)
            return
        for row in keys:
            if row["id"] == "key-default" and row.get("fingerprint") in ("SHA256:unknown", ""):
                self.db.update_ssh_key_fp("key-default", fp)

    def render_authorized_keys(self) -> Path:
        """把数据库里的公钥渲染成 sshd -A 使用的全局授权文件。"""
        lines = ["# 由 MosBox Panel 自动生成 —— 请在面板「SSH」页管理公钥"]
        for row in self.db.list_ssh_keys():
            if parse_public_key(row["key_line"]):
                lines.append(row["key_line"].strip())
        tmp = self.authorized_keys.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.authorized_keys)
        try:
            os.chmod(self.authorized_keys, 0o600)
        except OSError:
            pass
        return self.authorized_keys

    def sync_keys(self) -> None:
        """公钥变更后:重渲染授权文件并重启 sshd 使其生效。"""
        self.reload()

    def reload(self) -> None:
        """公钥变更后让 sshd 生效。

        puressh 的 -A 授权文件在启动时加载,且 SIGHUP 会让守护进程 re-exec
        脱离编排器监控 —— 因此这里直接走"干净重启":SIGTERM 后由编排器拉活,
        已建立的会话不受影响(会话子进程会被 reparent 到 PID 1)。
        """
        self.render_authorized_keys()
        if self.restart_cb:
            self.restart_cb()
            return
        # 兜底:SIGHUP(仅当编排器未注入重启回调时)
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGHUP)
            except OSError:
                pass

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    @property
    def self_test(self) -> dict:
        ok = self.running and bool(self.authorized_keys.exists())
        return {
            "ok": ok,
            "detail": "sshd 运行中" if ok else "sshd 未运行",
        }

    @staticmethod
    def supported_features() -> list[str]:
        return ["shell", "exec", "sftp", "scp",
                "port-forwarding(-L/-R/-D)", "agent-forwarding"]
