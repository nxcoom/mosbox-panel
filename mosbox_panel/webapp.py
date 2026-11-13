"""Web API(FastAPI):面板全部 HTTP/WS 接口 + 开发者兼容 API。

2.0:静态前端改由 raddy 直出(本文件的 SPA 兜底仅在直连 uvicorn 时生效);
SSH 服务改由 puressh sshd 提供,这里只管理公钥(经 SshManager 渲染+重载)。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse, HTMLResponse

from . import APP_NAME, APP_VERSION
from . import auth as authmod
from .ssh import parse_public_key
from .supervisor import STATE_RUNNING

MAX_TEXT_EDIT = 1024 * 1024          # 1MB 在线编辑上限
MAX_UPLOAD = 64 * 1024 * 1024        # 64MB 上传上限
PREVIEW_MAX = 2 * 1024 * 1024

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

TEMPLATES = {
    "blank": {"name": "空白程序", "files": {
        "main.py": (
            "import time\n\n"
            "print('程序已启动,输出会实时显示在面板里。')\n"
            "while True:\n"
            "    print(time.strftime('[%H:%M:%S] 心跳 ok'))\n"
            "    time.sleep(5)\n"
        )
    }},
    "http-server": {"name": "HTTP 服务示例(零依赖)", "files": {
        "main.py": (
            "import http.server, socketserver, os\n\n"
            "PORT = int(os.environ.get('PORT', '8080'))\n\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'text/plain; charset=utf-8')\n"
            "        self.end_headers()\n"
            "        self.wfile.write('Hello from MosBox Panel!\\n'.encode())\n"
            "    def log_message(self, *a):\n"
            "        print('[http]', *a)\n\n"
            "with socketserver.TCPServer(('0.0.0.0', PORT), H) as srv:\n"
            "    print(f'listening on {PORT}')\n"
            "    srv.serve_forever()\n"
        )
    }},
    "scheduler": {"name": "定时任务示例", "files": {
        "main.py": (
            "import time, datetime\n\n"
            "print('定时任务已启动,每 10 秒执行一次。')\n"
            "while True:\n"
            "    print(datetime.datetime.now().isoformat(), '执行任务')\n"
            "    time.sleep(10)\n"
        )
    }},
}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
    return s or "app"


class FileOps:
    """受根目录约束的文件操作(防路径穿越)。"""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def resolve(self, rel: str) -> Path:
        rel = (rel or "").lstrip("/")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError("路径越界")
        return p

    def list_dir(self, rel: str = "") -> dict:
        base = self.resolve(rel)
        if not base.exists():
            base.mkdir(parents=True, exist_ok=True)
        if not base.is_dir():
            raise NotADirectoryError(rel)
        items = []
        for child in sorted(base.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            st = child.stat()
            items.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": 0 if child.is_dir() else st.st_size,
                "mtime": int(st.st_mtime),
            })
        return {"path": base.relative_to(self.root).as_posix() if base != self.root else "",
                "items": items}

    def read_file(self, rel: str) -> str:
        p = self.resolve(rel)
        if p.is_dir():
            raise IsADirectoryError(rel)
        if p.stat().st_size > MAX_TEXT_EDIT:
            raise ValueError("文件过大,请下载后编辑")
        return p.read_text(encoding="utf-8", errors="replace")

    def write_file(self, rel: str, content: str) -> None:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def mkdir(self, rel: str) -> None:
        self.resolve(rel).mkdir(parents=True, exist_ok=True)

    def delete(self, rel: str) -> None:
        p = self.resolve(rel)
        if p == self.root:
            raise PermissionError("不能删除根目录")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)

    def rename(self, rel: str, new_name: str) -> None:
        if "/" in new_name or ".." in new_name or not new_name.strip():
            raise ValueError("非法名称")
        p = self.resolve(rel)
        p.rename(p.with_name(new_name))

    def set_exec(self, rel: str, on: bool = True) -> None:
        """设置/取消可执行位(二进制程序需要)。"""
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        st = p.stat().st_mode
        p.chmod(st | 0o111 if on else st & ~0o111)

    def save_upload(self, rel: str, data: bytes) -> None:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def make_app(ctx) -> FastAPI:
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
    # HTTP 直接由 uvicorn 承载(2.0 起 raddy 只做 L4 SSH 分流):
    # gzip 补回 raddy encode 的压缩;安全头补回 raddy header 指令
    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp
    app.state.ctx = ctx
    db, cfg, sup, events, auth, r2, stats = (
        ctx.db, ctx.cfg, ctx.supervisor, ctx.events, ctx.auth, ctx.r2store, ctx.stats)

    shared_ops = FileOps(ctx.dirs["files"])

    # ================= 认证与基础 =================
    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True  # 非浏览器客户端
        try:
            host = request.headers.get("host", "")
            return origin.split("://", 1)[1].split("/")[0] == host
        except Exception:
            return False

    def client_auth(request: Request) -> str | None:
        """返回 'session' | 'apikey:<name>' | None。"""
        token = request.cookies.get(authmod.COOKIE_NAME)
        if token and auth.check_session(token):
            return "session"
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            if auth.is_api_token(token):
                row = db.get_api_key_by_token(token)
                if row:
                    db.touch_api_key(row["id"])
                    return f"apikey:{row['name']}"
        return None

    def require_auth(request: Request):
        who = client_auth(request)
        if not who:
            raise HTTPException(status_code=401, detail="未登录")
        if who == "session" and request.method in ("POST", "PUT", "DELETE", "PATCH") \
                and not same_origin(request):
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")
        return who

    def guard(request: Request):
        require_auth(request)

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.json()
        ip = await authmod.resolve_client(request)
        if auth.throttle.blocked(ip):
            return JSONResponse({"detail": "尝试过于频繁,请稍后再试"}, status_code=429)
        if not auth.verify_login(str(body.get("password", ""))):
            auth.throttle.record_fail(ip)
            events.emit("auth.fail", f"登录失败 {ip}", {"ip": ip}, to_db=False)
            return JSONResponse({"detail": "密码错误"}, status_code=401)
        auth.throttle.reset(ip)
        token = auth.create_session()
        resp = JSONResponse({"ok": True, "name": cfg.get("panel_name"), "version": APP_VERSION})
        resp.set_cookie(authmod.COOKIE_NAME, token, max_age=authmod.SESSION_TTL,
                        httponly=True, samesite="lax", path="/")
        # 首次登录后移除明文密码提示文件
        with contextlib.suppress(OSError):
            (ctx.dirs["root"] / "initial_password.txt").unlink(missing_ok=True)
        events.emit("auth.login", f"面板登录成功 {ip}", {"ip": ip})
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(authmod.COOKIE_NAME)
        if token:
            auth.drop_session(token)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(authmod.COOKIE_NAME, path="/")
        return resp

    @app.get("/api/auth/me")
    async def me(request: Request):
        who = client_auth(request)
        return {
            "authenticated": bool(who),
            "panel_name": cfg.get("panel_name"),
            "version": APP_VERSION,
            "public_url": cfg.get("public_url", ""),
            "first_login": (ctx.dirs["root"] / "initial_password.txt").exists(),
        }

    # ================= 系统/仪表盘/事件 =================
    @app.get("/api/system/stats")
    async def system_stats(request: Request):
        guard(request)
        return {"system": stats.system, "workloads": stats.workloads}

    @app.get("/api/dashboard")
    async def dashboard(request: Request):
        guard(request)
        rows = sup.list_all()
        running = [w for w in rows if w["status"] == STATE_RUNNING]
        ssh_conf = cfg.get("ssh", {})
        return {
            "system": stats.system,
            "workloads": [{"id": w["id"], "name": w["name"], "status": w["status"],
                           "auto_restart": bool(w.get("auto_restart")),
                           "autostart": bool(w.get("autostart")),
                           "uptime": w.get("uptime"),
                           "stats": stats.workloads.get(w["id"], {})} for w in rows],
            "running_count": len(running),
            "total_count": len(rows),
            "events": events.db.list_events(15),
            "ssh": {"enabled": ssh_conf.get("enabled", True), "port": cfg.port,
                    "host_key_fp": ctx.ssh.host_key_fp, "key_count": len(db.list_ssh_keys()),
                    "user": getattr(ctx.ssh, "user", "root")},
            "r2": r2.public_conf(),
            "version": APP_VERSION,
        }

    @app.get("/api/events")
    async def list_events(request: Request, limit: int = 100):
        guard(request)
        return {"events": db.list_events(min(limit, 500))}

    # ================= 工作负载 =================
    def wl_view(w: dict) -> dict:
        return {
            "id": w["id"], "name": w["name"], "entrypoint": w.get("entrypoint", ""),
            "command": w.get("command", ""), "auto_restart": bool(w.get("auto_restart")),
            "autostart": bool(w.get("autostart")), "env": w.get("env") or {},
            "created_at": w.get("created_at"), "restarts": w.get("restarts", 0),
            "last_exit": w.get("last_exit"), "last_started": w.get("last_started"),
            "status": w["status"], "pid": w.get("pid"), "uptime": w.get("uptime"),
            "stats": stats.workloads.get(w["id"], {}),
        }

    @app.get("/api/workloads")
    async def workloads(request: Request):
        guard(request)
        return {"workloads": [wl_view(w) for w in sup.list_all()]}

    @app.post("/api/workloads")
    async def create_workload(request: Request):
        guard(request)
        body = await request.json()
        name = str(body.get("name") or "").strip() or slugify("app")
        wid = f"wl-{slugify(name)}-{os.urandom(3).hex()}"
        if db.get_workload(wid):
            return JSONResponse({"detail": "名称冲突"}, status_code=409)
        w = db.create_workload(
            wid, name, entrypoint=str(body.get("entrypoint") or "main.py"),
            command=str(body.get("command") or ""),
            auto_restart=bool(body.get("auto_restart", True)),
            autostart=bool(body.get("autostart", False)),
            env=body.get("env") or {})
        wdir = sup.workload_dir(wid)
        seed = body.get("seed") or {}
        template = body.get("template")
        if template in TEMPLATES:
            seed = TEMPLATES[template]["files"]
        for fname, content in seed.items():
            p = (wdir / fname).resolve()
            if wdir in p.parents or p.parent == wdir:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(content), encoding="utf-8")
        # 自动探测入口文件
        entry = w.get("entrypoint") or ""
        if not (wdir / entry).exists():
            for cand in ("main.py", "bot.py", "app.py", "index.py"):
                if (wdir / cand).exists():
                    entry = cand
                    break
            else:
                pyfiles = sorted(wdir.glob("*.py"))
                entry = pyfiles[0].name if pyfiles else "main.py"
        db.update_workload(wid, entrypoint=entry)
        events.emit("workload.create", f"创建程序 {name}", {"id": wid})
        return {"workload": wl_view(db.get_workload(wid))}

    # ---- 程序创建核心(单文件直传与分块上传共用) ----
    def _create_workload_from_file(base_name: str, fname: str, data: bytes) -> dict:
        wid = f"wl-{slugify(base_name)}-{os.urandom(3).hex()}"
        if db.get_workload(wid):
            raise ValueError("名称冲突")
        wdir = sup.workload_dir(wid)
        entry = "main.py"
        if fname.lower().endswith(".zip"):
            tmp = ctx.dirs["run"] / f"upload-{wid}.zip"
            tmp.write_bytes(data)
            sup.import_zip(wid, tmp)
            tmp.unlink(missing_ok=True)
            for cand in ("main.py", "bot.py", "app.py", "index.py"):
                if (wdir / cand).exists():
                    entry = cand
                    break
            else:
                pyfiles = sorted(wdir.rglob("*.py"))
                if pyfiles:
                    entry = pyfiles[0].relative_to(wdir).as_posix()
                else:
                    # 没有 .py:找可执行程序(ELF / 带执行位),按路径最短优先
                    prog = sorted((p for p in wdir.rglob("*")
                                   if sup._entry_is_program(p)),
                                  key=lambda p: len(p.parts))
                    entry = prog[0].relative_to(wdir).as_posix() if prog else "main.py"
        elif fname.lower().endswith(".py"):
            (wdir / fname).write_bytes(data)
            entry = fname
        else:
            # 任意文件(典型:ELF 二进制、带 shebang 的脚本、node 程序等)
            (wdir / fname).write_bytes(data)
            if data[:4] == b"\x7fELF":
                with contextlib.suppress(OSError):
                    os.chmod(wdir / fname, 0o755)
            entry = fname
        w = db.create_workload(wid, base_name, entrypoint=entry,
                               auto_restart=True, autostart=False)
        events.emit("workload.create", f"上传创建程序 {base_name}", {"id": wid})
        return wl_view(w)

    @app.post("/api/workloads/upload")
    async def upload_workload(request: Request,
                              file: UploadFile = File(...), name: str = Form("")):
        guard(request)
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"detail": "文件超过 64MB 限制"}, status_code=413)
        fname = file.filename or "app"
        base_name = (name or Path(fname).stem or "app").strip()
        try:
            return {"workload": _create_workload_from_file(base_name, fname, data)}
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)

    # ---- 分块上传(botkeep 平台端口转发对 >16KB 的请求体丢包,大文件
    #      必须切片;同时也让受限网络下的上传更稳) ----
    CHUNK_SIZE = 8 * 1024
    _upload_sessions: dict[str, dict] = {}

    @app.post("/api/upload/begin")
    async def upload_begin(request: Request):
        guard(request)
        body = await request.json()
        token = secrets.token_hex(8)
        udir = ctx.dirs["run"] / "uploads" / token
        udir.mkdir(parents=True, exist_ok=True)
        # 清理超过 2 小时的僵尸会话
        now = time.time()
        for tk, sess in list(_upload_sessions.items()):
            if now - sess["created"] > 7200:
                with contextlib.suppress(Exception):
                    shutil.rmtree(sess["dir"], ignore_errors=True)
                _upload_sessions.pop(tk, None)
        _upload_sessions[token] = {"dir": udir, "created": now,
                                   "name": Path(str(body.get("name") or "file.bin")).name}
        return {"ok": True, "token": token, "chunk": CHUNK_SIZE}

    @app.post("/api/upload/chunk")
    async def upload_chunk(request: Request):
        guard(request)
        body = await request.json()
        sess = _upload_sessions.get(str(body.get("token") or ""))
        if not sess:
            return JSONResponse({"detail": "上传会话不存在或已过期"}, status_code=404)
        seq = int(body.get("seq", -1))
        if seq < 0 or seq > 20000:
            return JSONResponse({"detail": "seq 非法"}, status_code=400)
        try:
            data = base64.b64decode(str(body.get("data") or ""))
        except Exception:
            return JSONResponse({"detail": "data 非法"}, status_code=400)
        if len(data) > 128 * 1024:
            return JSONResponse({"detail": "分块过大"}, status_code=400)
        (sess["dir"] / f"part{seq:05d}").write_bytes(data)
        return {"ok": True, "received": seq}

    @app.post("/api/upload/finish")
    async def upload_finish(request: Request):
        guard(request)
        body = await request.json()
        token = str(body.get("token") or "")
        sess = _upload_sessions.pop(token, None)
        if not sess:
            return JSONResponse({"detail": "上传会话不存在"}, status_code=404)
        udir = sess["dir"]
        parts = sorted(udir.glob("part*"))
        if not parts:
            return JSONResponse({"detail": "没有收到任何分块"}, status_code=400)
        assembled = udir / sess["name"]
        total = 0
        with open(assembled, "wb") as out:
            for p in parts:
                chunk = p.read_bytes()
                total += len(chunk)
                out.write(chunk)
                p.unlink(missing_ok=True)
        if total > MAX_UPLOAD:
            assembled.unlink(missing_ok=True)
            return JSONResponse({"detail": "文件超过 64MB 限制"}, status_code=413)
        base_name = (str(body.get("name") or "") or Path(sess["name"]).stem or "app").strip()
        try:
            wl = _create_workload_from_file(base_name, sess["name"], assembled.read_bytes())
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        finally:
            with contextlib.suppress(OSError):
                assembled.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                udir.rmdir()
        return {"workload": wl}
        w = db.create_workload(wid, base_name, entrypoint=entry,
                               auto_restart=True, autostart=False)
        events.emit("workload.create", f"上传创建程序 {base_name}", {"id": wid})
        return {"workload": wl_view(w)}

    @app.get("/api/workloads/{wid}")
    async def get_workload(request: Request, wid: str):
        guard(request)
        w = db.get_workload(wid)
        if not w:
            return JSONResponse({"detail": "不存在"}, status_code=404)
        return {"workload": wl_view(sup.get_state(wid) or w)}

    @app.put("/api/workloads/{wid}")
    async def update_workload(request: Request, wid: str):
        guard(request)
        if not db.get_workload(wid):
            return JSONResponse({"detail": "不存在"}, status_code=404)
        body = await request.json()
        fields = {}
        for k in ("name", "entrypoint", "command"):
            if k in body and body[k] is not None:
                fields[k] = str(body[k])
        for k in ("auto_restart", "autostart"):
            if k in body and body[k] is not None:
                fields[k] = bool(body[k])
        if "env" in body and isinstance(body["env"], dict):
            fields["env"] = body["env"]
        db.update_workload(wid, **fields)
        events.emit("workload.update", f"更新程序配置 {wid}", {"id": wid})
        return {"workload": wl_view(db.get_workload(wid))}

    @app.delete("/api/workloads/{wid}")
    async def delete_workload(request: Request, wid: str):
        guard(request)
        w = db.get_workload(wid)
        if not w:
            return JSONResponse({"detail": "不存在"}, status_code=404)
        await sup.stop_workload(wid)
        shutil.rmtree(sup.workload_dir(wid), ignore_errors=True)
        sup.log_path(wid).unlink(missing_ok=True)
        db.delete_workload(wid)
        events.emit("workload.delete", f"删除程序 {w['name']}", {"id": wid})
        return {"ok": True}

    @app.post("/api/workloads/{wid}/actions")
    async def workload_action(request: Request, wid: str):
        guard(request)
        body = await request.json()
        action = body.get("action")
        if not db.get_workload(wid):
            return JSONResponse({"detail": "不存在"}, status_code=404)
        if action == "start":
            result = await sup.start_workload(wid)
        elif action == "stop":
            result = await sup.stop_workload(wid)
        elif action == "restart":
            result = await sup.restart_workload(wid)
        else:
            return JSONResponse({"detail": "未知操作"}, status_code=400)
        if not result.get("ok", True) and result.get("reason"):
            return JSONResponse({"detail": result["reason"]}, status_code=409)
        return {"ok": True, "workload": wl_view(db.get_workload(wid))}

    @app.post("/api/workloads/{wid}/install-deps")
    async def install_deps(request: Request, wid: str):
        guard(request)
        if not db.get_workload(wid):
            return JSONResponse({"detail": "不存在"}, status_code=404)
        ok = await sup.install_deps(wid, force=True)
        return {"ok": ok}

    @app.post("/api/workloads/{wid}/stdin")
    async def workload_stdin(request: Request, wid: str):
        guard(request)
        body = await request.json()
        ok = sup.write_stdin(wid, str(body.get("input", "")))
        return {"ok": ok}

    @app.get("/api/workloads/{wid}/logs")
    async def workload_logs(request: Request, wid: str, lines: int = 200):
        guard(request)
        return {"lines": sup.read_log(wid, min(lines, 2000))}

    @app.get("/api/workloads/{wid}/export")
    async def export_workload(request: Request, wid: str):
        guard(request)
        w = db.get_workload(wid)
        if not w:
            return JSONResponse({"detail": "不存在"}, status_code=404)
        zpath = sup.export_zip(wid)
        if not zpath or not zpath.exists():
            return JSONResponse({"detail": "导出失败"}, status_code=500)
        return FileResponse(zpath, media_type="application/zip",
                            filename=f"{slugify(w['name'])}.zip")

    # ---- 程序文件管理 ----
    def wl_ops(wid: str) -> FileOps:
        if not db.get_workload(wid):
            raise HTTPException(status_code=404, detail="不存在")
        return FileOps(sup.workload_dir(wid))

    @app.get("/api/workloads/{wid}/files")
    async def wl_files(request: Request, wid: str, path: str = ""):
        guard(request)
        return wl_ops(wid).list_dir(path)

    @app.get("/api/workloads/{wid}/file")
    async def wl_read_file(request: Request, wid: str, path: str):
        guard(request)
        try:
            return {"path": path, "content": wl_ops(wid).read_file(path)}
        except IsADirectoryError:
            return JSONResponse({"detail": "这是一个目录"}, status_code=400)
        except (FileNotFoundError, ValueError) as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    @app.put("/api/workloads/{wid}/file")
    async def wl_write_file(request: Request, wid: str):
        guard(request)
        body = await request.json()
        try:
            wl_ops(wid).write_file(body["path"], str(body.get("content", "")))
        except Exception as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        events.emit("file.edit", f"编辑文件 {body['path']}", {"id": wid}, to_db=False)
        return {"ok": True}

    @app.post("/api/workloads/{wid}/file-action")
    async def wl_file_action(request: Request, wid: str):
        guard(request)
        body = await request.json()
        ops = wl_ops(wid)
        action = body.get("action")
        try:
            if action == "mkdir":
                ops.mkdir(body["path"])
            elif action == "delete":
                ops.delete(body["path"])
            elif action == "rename":
                ops.rename(body["path"], body["name"])
            elif action == "chmod":
                ops.set_exec(body["path"], bool(body.get("on", True)))
            else:
                return JSONResponse({"detail": "未知操作"}, status_code=400)
        except FileNotFoundError as e:
            return JSONResponse({"detail": "文件不存在"}, status_code=404)
        except (PermissionError, ValueError, OSError) as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {"ok": True}

    @app.post("/api/workloads/{wid}/files/upload")
    async def wl_upload_file(request: Request, wid: str,
                             file: UploadFile = File(...), path: str = Form("")):
        guard(request)
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"detail": "文件超过 64MB 限制"}, status_code=413)
        rel = (path or "").strip("/")
        target = f"{rel}/{file.filename}" if rel else file.filename
        try:
            wl_ops(wid).save_upload(target, data)
        except (PermissionError, OSError) as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {"ok": True, "path": target}

    @app.get("/api/workloads/{wid}/download")
    async def wl_download(request: Request, wid: str, path: str):
        guard(request)
        try:
            p = wl_ops(wid).resolve(path)
            if p.is_file():
                return FileResponse(p, filename=p.name)
            return JSONResponse({"detail": "不是文件"}, status_code=400)
        except PermissionError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    # ================= 公共文件区 =================
    @app.get("/api/files")
    async def shared_files(request: Request, path: str = ""):
        guard(request)
        return shared_ops.list_dir(path)

    @app.get("/api/files/file")
    async def shared_read(request: Request, path: str):
        guard(request)
        try:
            return {"path": path, "content": shared_ops.read_file(path)}
        except Exception as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    @app.put("/api/files/file")
    async def shared_write(request: Request):
        guard(request)
        body = await request.json()
        try:
            shared_ops.write_file(body["path"], str(body.get("content", "")))
        except Exception as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {"ok": True}

    @app.post("/api/files/file-action")
    async def shared_action(request: Request):
        guard(request)
        body = await request.json()
        action = body.get("action")
        try:
            if action == "mkdir":
                shared_ops.mkdir(body["path"])
            elif action == "delete":
                shared_ops.delete(body["path"])
            elif action == "rename":
                shared_ops.rename(body["path"], body["name"])
            elif action == "chmod":
                shared_ops.set_exec(body["path"], bool(body.get("on", True)))
            else:
                return JSONResponse({"detail": "未知操作"}, status_code=400)
        except (PermissionError, ValueError, OSError, FileNotFoundError) as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {"ok": True}

    @app.post("/api/files/upload")
    async def shared_upload(request: Request, file: UploadFile = File(...), path: str = Form("")):
        guard(request)
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"detail": "文件超过 64MB 限制"}, status_code=413)
        rel = (path or "").strip("/")
        target = f"{rel}/{file.filename}" if rel else file.filename
        try:
            shared_ops.save_upload(target, data)
        except (PermissionError, OSError) as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {"ok": True, "path": target}

    @app.get("/api/files/download")
    async def shared_download(request: Request, path: str):
        guard(request)
        try:
            p = shared_ops.resolve(path)
            if p.is_file():
                return FileResponse(p, filename=p.name)
            return JSONResponse({"detail": "不是文件"}, status_code=400)
        except PermissionError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    # 未登录也可访问的公共文件直链(面板自带迷你 CDN/raddy file-server):/f/<path>
    @app.get("/f/{key:path}")
    async def public_panel_file(key: str):
        try:
            p = shared_ops.resolve(f"public/{key}")
        except PermissionError:
            return JSONResponse({"detail": "路径越界"}, status_code=400)
        if p.is_file():
            return FileResponse(p)
        if p.is_dir():
            index = p / "index.html"
            if index.exists():  # 静态站点直出
                return FileResponse(index)
            items = []
            for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
                items.append(f'<li><a href="/f/{quote(key + "/" + child.name).lstrip("/")}">'
                             f'{"📁" if child.is_dir() else "📄"} {esc(child.name)}</a></li>')
            return HTMLResponse(f"<h3>/{key}</h3><ul>{''.join(items)}</ul>")
        return JSONResponse({"detail": "不存在"}, status_code=404)

    # ================= WebSocket =================
    def ws_auth(ws: WebSocket) -> bool:
        token = ws.cookies.get(authmod.COOKIE_NAME)
        return auth.check_session(token)

    @app.websocket("/api/ws/events")
    async def ws_events(ws: WebSocket):
        if not ws_auth(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        q = await events.subscribe()
        try:
            await ws.send_json({"type": "hello", "version": APP_VERSION})
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    await ws.send_json({"type": "event", "event": ev})
                except asyncio.TimeoutError:
                    with contextlib.suppress(Exception):
                        await ws.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        finally:
            events.unsubscribe(q)

    @app.websocket("/api/ws/logs/{wid}")
    async def ws_logs(ws: WebSocket, wid: str):
        if not ws_auth(ws):
            await ws.close(code=4401)
            return
        if not db.get_workload(wid):
            await ws.close(code=4404)
            return
        await ws.accept()
        ch = sup.channel(wid)
        q = ch.subscribe()
        closed = asyncio.Event()

        async def sender():
            while not closed.is_set():
                try:
                    text = await asyncio.wait_for(q.get(), timeout=15)
                    await ws.send_json({"type": "log", "data": text})
                except asyncio.TimeoutError:
                    with contextlib.suppress(Exception):
                        await ws.send_json({"type": "ping"})
                except Exception:
                    closed.set()
                    return

        async def receiver():
            try:
                while not closed.is_set():
                    msg = await ws.receive_json()
                    if isinstance(msg, dict) and "input" in msg:
                        sup.write_stdin(wid, str(msg["input"]))
                    elif isinstance(msg, dict) and msg.get("action") == "stop":
                        await sup.stop_workload(wid)
            except WebSocketDisconnect:
                closed.set()
            except Exception:
                closed.set()

        try:
            await asyncio.gather(sender(), receiver())
        finally:
            ch.unsubscribe(q)
            with contextlib.suppress(Exception):
                await ws.close()

    @app.websocket("/api/ws/terminal")
    async def ws_terminal(ws: WebSocket):
        if not ws_auth(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        from .terminal import PTYSession, pick_shell, session_env

        token_file = ctx.dirs["run"] / "local_token"

        def on_output(data: bytes):
            with contextlib.suppress(Exception):
                asyncio.create_task(_send({"type": "data",
                                           "data": base64.b64encode(data).decode()}))

        def on_exit():
            with contextlib.suppress(Exception):
                asyncio.create_task(_send({"type": "exit"}))

        session: PTYSession | None = None

        async def _send(obj):
            await ws.send_json(obj)

        async def reader_loop():
            nonlocal session
            try:
                while True:
                    msg = await ws.receive_json()
                    mtype = msg.get("type")
                    if mtype == "start" and session is None:
                        cwd = str(ctx.dirs["root"])
                        s = PTYSession([pick_shell(), "-l"], cwd,
                                       session_env(ctx.dirs, ctx.panel_url, token_file),
                                       on_output, on_exit)
                        s.resize(int(msg.get("rows") or 24), int(msg.get("cols") or 80))
                        s.attach_reader()
                        session = s
                    elif mtype == "input" and session:
                        session.write(base64.b64decode(msg.get("data", "")))
                    elif mtype == "resize" and session:
                        session.resize(int(msg.get("rows") or 24),
                                       int(msg.get("cols") or 80))
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                if session:
                    session.close()

        try:
            await reader_loop()
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    # ================= SSH 管理(puressh sshd) =================
    @app.get("/api/ssh")
    async def ssh_info(request: Request):
        guard(request)
        keys = db.list_ssh_keys()
        return {
            "enabled": cfg.get("ssh", {}).get("enabled", True),
            "port": cfg.port,  # 与面板共用端口
            "host_key_fp": ctx.ssh.host_key_fp,
            "keys": keys,
            "allow_sftp": cfg.get("ssh", {}).get("allow_sftp", True),
            "self_test": ctx.ssh.self_test,
            "features": ctx.ssh.supported_features(),
            "server": "puressh sshd",
            "user": getattr(ctx.ssh, "user", "root"),
        }

    @app.post("/api/ssh/keys")
    async def add_ssh_key(request: Request):
        guard(request)
        body = await request.json()
        line = str(body.get("key") or "").strip()
        name = str(body.get("name") or "").strip() or "未命名密钥"
        parsed = parse_public_key(line)
        if not parsed:
            return JSONResponse({"detail": "无效的公钥格式"}, status_code=400)
        kid = f"key-{os.urandom(3).hex()}"
        db.add_ssh_key(kid, name, line, parsed[1])
        ctx.ssh.sync_keys()
        events.emit("ssh.key.add", f"添加 SSH 公钥 {name}", {"fingerprint": parsed[1]})
        return {"ok": True, "key": db.queryone("SELECT * FROM ssh_keys WHERE id=?", (kid,))}

    @app.delete("/api/ssh/keys/{kid}")
    async def del_ssh_key(request: Request, kid: str):
        guard(request)
        if kid == "key-default":
            return JSONResponse({"detail": "默认公钥不可删除"}, status_code=403)
        row = db.queryone("SELECT * FROM ssh_keys WHERE id=?", (kid,))
        db.delete_ssh_key(kid)
        ctx.ssh.sync_keys()
        if row:
            events.emit("ssh.key.remove", f"移除 SSH 公钥 {row['name']}")
        return {"ok": True}

    # ================= 存储(R2) =================
    @app.get("/api/storage/config")
    async def storage_config(request: Request):
        guard(request)
        return {"r2": r2.public_conf()}

    @app.put("/api/storage/config")
    async def storage_save(request: Request):
        guard(request)
        body = await request.json()
        conf = r2.save_config(body)
        r2.reset_client()
        events.emit("storage.config", "更新 R2 存储配置")
        return {"r2": conf}

    @app.post("/api/storage/test")
    async def storage_test(request: Request):
        guard(request)
        return r2.test()

    @app.get("/api/storage/backups")
    async def backups_list(request: Request):
        guard(request)
        items = []
        for b in db.list_backups():
            items.append(dict(b))
        return {"backups": items, "r2": r2.public_conf()}

    @app.post("/api/storage/backups")
    async def backup_create(request: Request):
        guard(request)
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        scope = body.get("scope", "all")
        name = str(body.get("name") or "").strip() or time.strftime("%Y%m%d-%H%M%S")
        zpath = await asyncio.to_thread(_build_backup_zip, ctx, scope)
        bid = f"bk-{os.urandom(3).hex()}"
        object_key = f"backups/{time.strftime('%Y%m%d-%H%M%S')}-{slugify(name)}-{bid}.zip"
        size = zpath.stat().st_size
        if r2.configured():
            await asyncio.to_thread(r2.put_file, object_key, zpath)
            storage = "r2"
            zpath.unlink(missing_ok=True)
        else:
            dest = ctx.dirs["backups"] / f"{bid}.zip"
            shutil.move(str(zpath), dest)
            object_key = str(dest)
            storage = "local"
        db.add_backup(bid, name, scope, storage, object_key, size)
        events.emit("backup.create", f"创建备份 {name} ({size // 1024} KB, {storage})",
                    {"id": bid, "storage": storage})
        return {"ok": True, "backup": db.get_backup(bid)}

    @app.post("/api/storage/backups/{bid}/restore")
    async def backup_restore(request: Request, bid: str):
        guard(request)
        b = db.get_backup(bid)
        if not b:
            return JSONResponse({"detail": "备份不存在"}, status_code=404)
        tmp = ctx.dirs["run"] / f"restore-{bid}.zip"
        try:
            if b["storage"] == "r2":
                await asyncio.to_thread(r2.download_to, b["object_key"], tmp)
            else:
                shutil.copy(b["object_key"], tmp)
        except Exception as e:
            return JSONResponse({"detail": f"获取备份失败: {e}"}, status_code=500)
        await asyncio.to_thread(_restore_backup_zip, ctx, tmp)
        tmp.unlink(missing_ok=True)
        events.emit("backup.restore", f"从备份恢复 {b['name']}", {"id": bid})
        return {"ok": True}

    @app.delete("/api/storage/backups/{bid}")
    async def backup_delete(request: Request, bid: str):
        guard(request)
        b = db.get_backup(bid)
        if not b:
            return JSONResponse({"detail": "备份不存在"}, status_code=404)
        with contextlib.suppress(Exception):
            if b["storage"] == "r2":
                r2.delete(b["object_key"])
            else:
                Path(b["object_key"]).unlink(missing_ok=True)
        db.delete_backup(bid)
        events.emit("backup.delete", f"删除备份 {b['name']}")
        return {"ok": True}

    @app.get("/api/storage/backups/{bid}/download")
    async def backup_download(request: Request, bid: str):
        guard(request)
        b = db.get_backup(bid)
        if not b:
            return JSONResponse({"detail": "备份不存在"}, status_code=404)
        if b["storage"] == "r2":
            url = r2.presigned_get(b["object_key"], 600)
            return RedirectResponse(url)
        p = Path(b["object_key"])
        if p.exists():
            return FileResponse(p, filename=f"{b['name']}.zip")
        return JSONResponse({"detail": "文件不存在"}, status_code=404)

    # ---- 公共文件(R2 优先,否则面板直链) ----
    @app.get("/api/storage/public")
    async def public_list(request: Request):
        guard(request)
        items = []
        if r2.configured():
            for obj in r2.list_prefix("public/"):
                key = obj["key"]
                items.append({"key": key[len("public/"):], "size": obj["size"],
                              "modified": obj["modified"], "url": r2.public_url(key)})
            return {"items": items, "backend": "r2"}
        pub = shared_ops.resolve("public")
        pub.mkdir(exist_ok=True)
        for p in sorted(pub.rglob("*")):
            if p.is_file():
                rel = p.relative_to(pub).as_posix()
                items.append({"key": rel, "size": p.stat().st_size,
                              "modified": int(p.stat().st_mtime),
                              "url": f"/f/{quote(rel)}"})
        return {"items": items, "backend": "panel"}

    @app.post("/api/storage/public/upload")
    async def public_upload(request: Request, file: UploadFile = File(...),
                            path: str = Form("")):
        guard(request)
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"detail": "文件超过 64MB 限制"}, status_code=413)
        rel = (path or "").strip("/")
        name = f"{rel}/{file.filename}" if rel else (file.filename or "file.bin")
        if r2.configured():
            key = f"public/{name}"
            ctype = file.content_type or "application/octet-stream"
            await asyncio.to_thread(r2.put_bytes, key, data, ctype)
            url = r2.public_url(key)
        else:
            shared_ops.save_upload(f"public/{name}", data)
            url = f"/f/{quote(name)}"
        events.emit("storage.public", f"发布公共文件 {name}")
        return {"ok": True, "key": name, "url": url}

    @app.delete("/api/storage/public/{key:path}")
    async def public_delete(request: Request, key: str):
        guard(request)
        if r2.configured():
            r2.delete(f"public/{key}")
        else:
            with contextlib.suppress(Exception):
                shared_ops.delete(f"public/{key}")
        return {"ok": True}

    # ================= API Keys =================
    @app.get("/api/keys")
    async def api_keys_list(request: Request):
        guard(request)
        return {"keys": db.list_api_keys()}

    @app.post("/api/keys")
    async def api_keys_create(request: Request):
        guard(request)
        body = await request.json()
        name = str(body.get("name") or "").strip() or "未命名密钥"
        kid = f"ak-{os.urandom(3).hex()}"
        token = auth.new_api_token()
        db.add_api_key(kid, token, name)
        events.emit("apikey.create", f"创建 API 密钥 {name}")
        return {"ok": True, "id": kid, "token": token}

    @app.delete("/api/keys/{kid}")
    async def api_keys_delete(request: Request, kid: str):
        guard(request)
        db.delete_api_key(kid)
        events.emit("apikey.delete", f"删除 API 密钥 {kid}")
        return {"ok": True}

    # ================= 设置 =================
    @app.get("/api/settings")
    async def get_settings(request: Request):
        guard(request)
        return {
            "panel_name": cfg.get("panel_name"),
            "port": cfg.port,
            "public_url": cfg.get("public_url", ""),
            "limits": cfg.get("limits"),
            "supervisor": cfg.get("supervisor"),
            "ssh": cfg.get("ssh"),
        }

    @app.put("/api/settings")
    async def put_settings(request: Request):
        guard(request)
        body = await request.json()
        if body.get("panel_name"):
            cfg.set("panel_name", str(body["panel_name"]).strip()[:40])
        if "public_url" in body:
            cfg.set("public_url", str(body.get("public_url") or "").strip())
        if body.get("current_password") and body.get("new_password"):
            try:
                ok = auth.change_password(str(body["current_password"]),
                                          str(body["new_password"]))
            except ValueError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)
            if not ok:
                return JSONResponse({"detail": "当前密码错误"}, status_code=403)
            events.emit("auth.password", "管理密码已修改")
        sup_conf = body.get("supervisor")
        if isinstance(sup_conf, dict):
            cur = dict(cfg.get("supervisor", {}))
            for k in ("auto_restart", "backoff_max", "log_rotate_mb", "log_keep"):
                if k in sup_conf:
                    cur[k] = sup_conf[k]
            cfg.set("supervisor", cur)
        events.emit("settings.update", "更新面板设置")
        return {"ok": True}

    # ================= 开发者兼容 API(bk_live_ 令牌) =================
    def api_key_auth(request: Request) -> dict | None:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            if auth.is_api_token(token):
                row = db.get_api_key_by_token(token)
                if row:
                    db.touch_api_key(row["id"])
                    return row
        return None

    @app.get("/api/v1/developer/capabilities")
    async def dev_capabilities(request: Request):
        if not api_key_auth(request):
            return JSONResponse({"detail": "Bearer key required"}, status_code=401)
        return {
            "provisioning": False,
            "developerKeys": True,
            "network": True,
            "sftp": True,
            "backupRestore": True,
            "backupDownload": True,
            "ssh": True,
            "selfHosted": True,
        }

    @app.get("/api/v1/developer/plan")
    async def dev_plan(request: Request):
        if not api_key_auth(request):
            return JSONResponse({"detail": "Bearer key required"}, status_code=401)
        lim = cfg.get("limits", {})
        return {"plan": "self-hosted", "planName": "Self-Hosted",
                "limits": {"slots": lim.get("slots", 64), "memory": lim.get("memory_mb", 512),
                           "cpu": lim.get("cpu_percent", 50), "disk": lim.get("storage_mb", 1024)}}

    @app.get("/api/v1/developer/workloads")
    async def dev_workloads(request: Request):
        if not api_key_auth(request):
            return JSONResponse({"detail": "Bearer key required"}, status_code=401)
        data = []
        for w in sup.list_all():
            st = stats.workloads.get(w["id"], {})
            data.append({
                "id": w["id"], "name": w["name"], "runtime": "python",
                "state": w["status"],
                "observedAt": int(time.time() * 1000),
                "resources": {"memoryMb": st.get("memory_mb", 0.0),
                              "cpuPercent": st.get("cpu_percent", 0.0),
                              "storageMb": 0},
            })
        return {"data": data, "nextCursor": None}

    @app.post("/api/v1/developer/workloads")
    async def dev_create(request: Request):
        if not api_key_auth(request):
            return JSONResponse({"detail": "Bearer key required"}, status_code=401)
        if not request.headers.get("idempotency-key"):
            return JSONResponse({"detail": "Idempotency-Key required"}, status_code=400)
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"detail": "name required"}, status_code=422)
        wid = f"wl-{slugify(name)}-{os.urandom(3).hex()}"
        if db.get_workload(wid):
            return JSONResponse({"detail": "名称冲突"}, status_code=409)
        db.create_workload(wid, name, entrypoint=str(body.get("entrypoint") or "main.py"),
                           command=str(body.get("startCommand") or ""),
                           auto_restart=True, autostart=True,
                           env=body.get("env") or {})
        seed = body.get("seedFiles") or {}
        wdir = sup.workload_dir(wid)
        for fname, content in seed.items():
            p = (wdir / fname).resolve()
            if wdir in p.parents or p.parent == wdir:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(content), encoding="utf-8")
        events.emit("workload.create", f"[API] 创建程序 {name}", {"id": wid})
        op_id = f"op-{os.urandom(4).hex()}"
        return JSONResponse({"operation": {"id": op_id, "action": "create", "phase": "complete"},
                             "workload": {"id": wid, "name": name, "state": "stopped"}},
                            status_code=202)

    @app.post("/api/v1/developer/workloads/{wid}/actions")
    async def dev_action(request: Request, wid: str):
        if not api_key_auth(request):
            return JSONResponse({"detail": "Bearer key required"}, status_code=401)
        body = await request.json()
        action = body.get("action")
        if not db.get_workload(wid):
            return JSONResponse({"detail": "workload not found"}, status_code=404)
        if action == "start":
            result = await sup.start_workload(wid)
        elif action == "stop":
            result = await sup.stop_workload(wid)
        elif action == "restart":
            result = await sup.restart_workload(wid)
        else:
            return JSONResponse({"detail": "unknown action"}, status_code=400)
        return JSONResponse({"operation": {"id": f"op-{os.urandom(4).hex()}",
                                           "action": action, "phase": "complete"},
                             "ok": result.get("ok", True)}, status_code=202)

    # ================= botkeepctl 本机控制接口(仅回环 + 本地令牌) =================
    def ctl_auth(request: Request) -> bool:
        peer = request.client.host if request.client else ""
        if peer not in ("127.0.0.1", "::1", "localhost"):
            return False
        token_file = ctx.dirs["run"] / "local_token"
        if not token_file.exists():
            return False
        supplied = request.headers.get("x-bkp-token", "")
        import hmac as _hmac
        return _hmac.compare_digest(supplied, token_file.read_text(encoding="utf-8").strip())

    def _ctl_find(name_or_id: str):
        for w in sup.list_all():
            if w["id"] == name_or_id or w["name"] == name_or_id:
                return w
        for w in sup.list_all():
            if name_or_id.lower() in w["name"].lower():
                return w
        return None

    @app.get("/api/ctl/ps")
    async def ctl_ps(request: Request):
        if not ctl_auth(request):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        rows = sup.list_all()
        running = [w for w in rows if w["status"] == STATE_RUNNING]
        return {"system": stats.system, "running_count": len(running),
                "workloads": [{"id": w["id"], "name": w["name"], "status": w["status"],
                               "pid": w.get("pid"), "restarts": w.get("restarts", 0),
                               "auto_restart": bool(w.get("auto_restart"))} for w in rows]}

    @app.get("/api/ctl/logs")
    async def ctl_logs(request: Request):
        if not ctl_auth(request):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        wid = request.query_params.get("id", "")
        lines = int(request.query_params.get("lines", "50"))
        return {"lines": sup.read_log(wid, min(lines, 1000))}

    @app.post("/api/ctl/{action}")
    async def ctl_action(request: Request, action: str):
        if not ctl_auth(request):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        w = _ctl_find(str(body.get("id") or ""))
        if not w:
            return JSONResponse({"detail": "找不到程序"}, status_code=404)
        if action == "start":
            result = await sup.start_workload(w["id"])
        elif action == "stop":
            result = await sup.stop_workload(w["id"])
        elif action == "restart":
            result = await sup.restart_workload(w["id"])
        else:
            return JSONResponse({"detail": "未知操作"}, status_code=400)
        return {"ok": result.get("ok", True), "detail": result.get("reason", "")}

    # ================= raddy 风格反向代理:把容器内程序端口暴露出去 =================
    import httpx

    _proxy_client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=300))

    HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                   "te", "trailers", "transfer-encoding", "upgrade", "host"}

    def _check_proxy_target(request: Request, wid: str, port: int):
        guard(request)
        if not db.get_workload(wid):
            raise HTTPException(status_code=404, detail="workload 不存在")
        if not (1 <= port <= 65535):
            raise HTTPException(status_code=400, detail="非法端口")

    async def _forward_http(request: Request, wid: str, port: int, path: str):
        _check_proxy_target(request, wid, port)
        url = f"http://127.0.0.1:{port}/{path}"
        if request.url.query:
            url += "?" + str(request.url.query)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        headers["host"] = f"127.0.0.1:{port}"
        body = await request.body()
        try:
            resp = await _proxy_client.request(
                request.method, url, headers=headers, content=body,
                follow_redirects=False)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return JSONResponse({"detail": f"容器内 127.0.0.1:{port} 无响应(程序未监听该端口?)"},
                                status_code=502)
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_HEADERS}
        return StreamingResponse(resp.aiter_bytes(), status_code=resp.status_code,
                                 headers=out_headers, media_type=resp.headers.get("content-type"))

    @app.api_route("/p/{wid}/{port}/{path:path}", methods=["GET", "POST", "PUT", "DELETE",
                                                            "PATCH", "HEAD", "OPTIONS"])
    async def proxy_http(request: Request, wid: str, port: int, path: str):
        return await _forward_http(request, wid, port, path)

    @app.api_route("/p/{wid}/{port}", methods=["GET", "POST", "PUT", "DELETE",
                                               "PATCH", "HEAD", "OPTIONS"])
    async def proxy_http_root(request: Request, wid: str, port: int):
        return await _forward_http(request, wid, port, "")

    @app.websocket("/p/{wid}/{port}/{path:path}")
    async def proxy_ws(ws: WebSocket, wid: str, port: int, path: str):
        if not ws_auth(ws):
            await ws.close(code=4401)
            return
        if not db.get_workload(wid) or not (1 <= port <= 65535):
            await ws.close(code=4404)
            return
        await ws.accept()
        import websockets as _wslib
        qs = str(ws.url.query)
        target = f"ws://127.0.0.1:{port}/{path}" + (f"?{qs}" if qs else "")
        try:
            async with _wslib.connect(target, max_size=None) as up:
                async def pump_out():  # 容器 → 浏览器
                    async for msg in up:
                        await ws.send_text(msg) if isinstance(msg, str) \
                            else await ws.send_bytes(msg)
                async def pump_in():   # 浏览器 → 容器
                    while True:
                        msg = await ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            return
                        if "text" in msg and msg["text"] is not None:
                            await up.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await up.send(msg["bytes"])
                await asyncio.gather(pump_out(), pump_in())
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    @app.websocket("/p/{wid}/{port}")
    async def proxy_ws_root(ws: WebSocket, wid: str, port: int):
        await proxy_ws(ws, wid, port, "")

    # ================= 静态资源 & SPA =================
    @app.get("/robots.txt", include_in_schema=False)
    async def robots():
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        cand = (FRONTEND_DIR / full_path).resolve()
        try:
            cand.relative_to(FRONTEND_DIR)
        except ValueError:
            cand = FRONTEND_DIR / "index.html"
        if full_path and cand.is_file():
            return FileResponse(cand)
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse("<h1>frontend missing</h1>", status_code=500)

    return app


# ================= 备份实现 =================
def _build_backup_zip(ctx, scope: str) -> Path:
    """打包程序目录与元数据为 zip。"""
    out = ctx.dirs["run"] / f"backup-{os.urandom(4).hex()}.zip"
    programs = ctx.dirs["programs"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        meta = {
            "app": APP_VERSION,
            "created_at": time.time(),
            "scope": scope,
            "workloads": [],
            "ssh_keys": ctx.db.list_ssh_keys(),
        }
        for w in ctx.db.list_workloads():
            if scope.startswith("workload:") and w["id"] != scope.split(":", 1)[1]:
                continue
            meta["workloads"].append({k: w[k] for k in
                                      ("id", "name", "entrypoint", "command", "auto_restart",
                                       "autostart", "env", "created_at", "restarts")})
        zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        for path in sorted(programs.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(programs)
            top = rel.parts[0] if rel.parts else ""
            if scope.startswith("workload:") and top != scope.split(":", 1)[1]:
                continue
            if ".botkeep_deps_ok" in path.name:
                continue
            zf.write(path, f"programs/{rel.as_posix()}")
    return out


def _restore_backup_zip(ctx, zpath: Path) -> None:
    """在线程中恢复备份:用独立事件循环执行需要 await 的停止操作。"""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_restore_impl(ctx, zpath))
    finally:
        asyncio.set_event_loop(None)
        loop.close()


async def _restore_impl(ctx, zpath: Path) -> None:
    with zipfile.ZipFile(zpath) as zf:
        meta = {}
        try:
            meta = json.loads(zf.read("meta.json").decode("utf-8"))
        except KeyError:
            pass
        scope = meta.get("scope", "all")
        # 停止受影响程序
        for w in meta.get("workloads", []):
            await ctx.supervisor.stop_workload(w["id"])
        for info in zf.infolist():
            if info.filename.startswith("programs/"):
                rel = info.filename[len("programs/"):]
                if not rel or ".." in Path(rel).parts:
                    continue
                dest = ctx.dirs["programs"] / rel
                if scope.startswith("workload:") and rel.split("/")[0] != scope.split(":", 1)[1]:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        # 恢复工作负载元数据
        for w in meta.get("workloads", []):
            wid = w["id"]
            if ctx.db.get_workload(wid):
                ctx.db.update_workload(wid, **{k: w.get(k) for k in
                                               ("name", "entrypoint", "command", "env")
                                               if k in w})
            else:
                ctx.db.create_workload(
                    wid, w.get("name", wid), entrypoint=w.get("entrypoint", "main.py"),
                    command=w.get("command", ""),
                    auto_restart=bool(w.get("auto_restart", 1)),
                    autostart=bool(w.get("autostart", 0)), env=w.get("env") or {})
        if scope == "all":
            known = {w["id"] for w in meta.get("workloads", [])}
            for w in ctx.db.list_workloads():
                if w["id"] not in known:
                    await ctx.supervisor.stop_workload(w["id"])
                    ctx.db.delete_workload(w["id"])
