"""认证:管理密码(登录)+ 会话 Cookie + API Key(bk_live_* 前缀,兼容 botkeep 云风格)。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time

SESSION_TTL = 7 * 24 * 3600
COOKIE_NAME = "botkeep_session"
API_KEY_PREFIX = "bk_live_"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$", 2)
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=2**14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


class LoginThrottle:
    """简单按 IP 限速:5 分钟窗口内最多 8 次失败。"""

    def __init__(self, max_fail: int = 8, window: int = 300):
        self.max_fail = max_fail
        self.window = window
        self._fails: dict[str, list[float]] = {}

    def blocked(self, ip: str) -> bool:
        rec = self._fails.get(ip)
        if not rec:
            return False
        now = time.time()
        rec = [t for t in rec if now - t < self.window]
        self._fails[ip] = rec
        return len(rec) >= self.max_fail

    def record_fail(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)


class Auth:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sessions: dict[str, float] = {}   # token -> expiry
        self.throttle = LoginThrottle()
        self._bootstrap()

    def _bootstrap(self) -> None:
        # 环境变量密码优先(适合容器部署),否则读取 initial_password.txt 生成 hash
        env_pw = os.environ.get("BOTKEEP_PASSWORD")
        if env_pw:
            stored = self.cfg.get("password_hash") or ""
            if not stored.startswith("scrypt$") or not verify_password(env_pw, stored):
                self.cfg.set("password_hash", hash_password(env_pw))
            return
        stored = self.cfg.get("password_hash") or ""
        if stored.startswith("scrypt$"):
            return
        pw_file = self.cfg.data_dir / "initial_password.txt"
        pw = None
        if pw_file.exists():
            pw = pw_file.read_text(encoding="utf-8").strip()
        if not pw:
            import string
            pw = "".join(secrets.choice(string.ascii_letters + string.digits)
                         for _ in range(10))
            pw_file.write_text(f"{pw}\n", encoding="utf-8")
            try:
                pw_file.chmod(0o600)
            except OSError:
                pass
        self.cfg.set("password_hash", hash_password(pw))

    def verify_login(self, password: str) -> bool:
        return verify_password(password, self.cfg.get("password_hash") or "")

    def change_password(self, old: str, new: str) -> bool:
        if not self.verify_login(old):
            return False
        if len(new) < 6:
            raise ValueError("新密码至少 6 位")
        self.cfg.set("password_hash", hash_password(new))
        self.sessions.clear()
        return True

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + SESSION_TTL
        return token

    def drop_session(self, token: str) -> None:
        self.sessions.pop(token, None)

    def check_session(self, token: str | None) -> bool:
        if not token:
            return False
        expiry = self.sessions.get(token)
        if not expiry:
            return False
        if expiry < time.time():
            self.sessions.pop(token, None)
            return False
        return True

    # ---- API keys ----
    @staticmethod
    def new_api_token() -> str:
        return API_KEY_PREFIX + secrets.token_hex(20)

    @staticmethod
    def is_api_token(value: str) -> bool:
        return isinstance(value, str) and value.startswith(API_KEY_PREFIX)

    async def verify_api_key(self, db, token: str) -> dict | None:
        row = db.get_api_key_by_token(token)
        if not row:
            return None
        row_id = row["id"]
        db.touch_api_key(row_id)
        return row


async def resolve_client(request) -> str:
    ip = request.client.host if request.client else "?"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    return ip
