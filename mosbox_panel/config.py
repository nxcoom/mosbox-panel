"""面板配置:JSON 持久化 + 环境变量覆盖。

架构(2.0):单公网端口由 raddy(L4 分流)承载 —— SSH 交给 puressh sshd,
HTTP 交给 raddy 自身(静态前端 + 反代到本面板 API)。内部端口每次启动
自动挑选空闲端口,不写死,避免端口冲突导致的启动失败。
"""
from __future__ import annotations

import json
import os
import secrets
import string
from pathlib import Path

from . import APP_NAME, APP_VERSION

# SSH 公钥登录的默认公钥(首次启动写入授权列表,可在面板里增删)
DEFAULT_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBbOngF8zJjVVvAlDWjZnGE83jXrt3aDviPCqIIE5Ju6"
)
DEFAULT_KEY_NAME = "default"

DEFAULT_CONFIG = {
    "bind": "0.0.0.0",
    "port": int(os.environ.get("BOTKEEP_PORT") or os.environ.get("PORT")
                or os.environ.get("SERVER_PORT") or 8800),
    "panel_name": "MosBox 控制台",
    "public_url": "",
    "password_hash": "",
    "limits": {"memory_mb": 512, "cpu_percent": 50, "storage_mb": 1024, "backups": 20},
    "ssh": {"enabled": True, "allow_sftp": True},
    "r2": {
        "enabled": False,
        "account_id": "",
        "access_key": "",
        "secret_key": "",
        "bucket": "",
        "public_base": "",  # 例如 https://r2.example.com (R2 自定义域名) 或 r2.dev 公开地址
    },
    "supervisor": {
        "auto_restart": True,
        "backoff_max": 30,
        "log_rotate_mb": 2,
        "log_keep": 5,
    },
}


class Config:
    """JSON 持久化配置,支持环境变量覆盖端口。"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "config.json"
        self._d: dict = {}
        self.reload()

    def reload(self) -> None:
        if self.path.exists():
            try:
                self._d = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._d = {}
        merged = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
        self._deep_merge(merged, self._d)
        # 环境变量始终优先于文件中的端口
        env_port = os.environ.get("BOTKEEP_PORT") or os.environ.get("PORT") \
            or os.environ.get("SERVER_PORT")
        if env_port:
            merged["port"] = int(env_port)
        self._d = merged

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        for k, v in (override or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                Config._deep_merge(base[k], v)
            else:
                base[k] = v

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str, default=None):
        return self._d.get(key, default)

    def set(self, key: str, value) -> None:
        self._d[key] = value
        self.save()

    @property
    def port(self) -> int:
        return int(self._d["port"])

    @property
    def bind(self) -> str:
        return str(self._d.get("bind") or "0.0.0.0")


def _gen_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    # 去掉易混淆字符
    alphabet = alphabet.replace("l", "").replace("I", "").replace("O", "").replace("0", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_initial_password(cfg: Config) -> str | None:
    """首次启动生成随机管理密码,写入 data/initial_password.txt(0600)。

    设置了 BOTKEEP_PASSWORD 时使用环境变量(不落地明文)。
    返回首次生成的明文密码(仅首次返回,用于日志打印);后续登录成功后该文件会被删除。
    """
    pw_file = cfg.data_dir / "initial_password.txt"
    if os.environ.get("BOTKEEP_PASSWORD") or cfg.get("password_hash"):
        return None
    pw = _gen_password()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    pw_file.write_text(f"{pw}\n", encoding="utf-8")
    try:
        os.chmod(pw_file, 0o600)
    except OSError:
        pass
    return pw


def dirs(data_dir: Path) -> dict:
    d = {
        "root": data_dir,
        "run": data_dir / "run",        # 生成的 Caddyfile/sshd_config/临时文件
        "logs": data_dir / "logs",      # raddy/sshd/程序日志
        "programs": data_dir / "programs",   # 各工作负载目录
        "files": data_dir / "files",    # 公共文件区(文件管理器根目录)
        "bin": data_dir / "bin",        # botkeepctl 等自生成工具
        "backups": data_dir / "backups",     # 本地备份(R2 未配置时的落点)
        "ssh": data_dir / "ssh",        # 主机密钥 + authorized_keys
    }
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d
