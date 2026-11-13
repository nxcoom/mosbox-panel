"""一键部署 MosBox Panel 到 botkeep 容器(mos-box)。

用法:
    export BOTKEEP_API_KEY="bk_live_xxx"
    export BOTKEEP_WORKLOAD_ID="fa0461af-..."   # 可选,默认自动取第一个 workload
    python deploy.py

说明:只上传代码(main.py / mosbox_panel/ / frontend/ / requirements.txt),
raddy / puressh sshd 二进制在容器首次启动时由 main.py 从 GitHub Releases
自动下载(平台单文件上传接口放不下几十 MB 的二进制)。
"""
import base64
import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

API = "https://api.botkeep.cloud/api/v1/developer"
ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".venv", "__pycache__", "data", ".git", "bin", ".zcode"}


def call(method, path, body=None, idem=None):
    req = urllib.request.Request(API + path, method=method)
    key = os.environ["BOTKEEP_API_KEY"]
    req.add_header("Authorization", f"Bearer {key}")
    if idem:
        req.add_header("Idempotency-Key", idem)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise SystemExit(f"✗ {method} {path} -> HTTP {e.code}: {detail}")


def build_zip() -> bytes:
    bio = __import__("io").BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f.endswith((".pyc", ".log", ".zip")):
                    continue
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, ROOT))
    return bio.getvalue()


def main():
    if "BOTKEEP_API_KEY" not in os.environ:
        # 允许从上级目录 botkeep-api.txt 读取密钥
        cand = os.path.join(os.path.dirname(ROOT), "botkeep-api.txt")
        if os.path.exists(cand):
            for line in open(cand, encoding="utf-8"):
                line = line.strip()
                if line.startswith("bk_live_"):
                    os.environ["BOTKEEP_API_KEY"] = line
                    break
        else:
            raise SystemExit("✗ 请先 export BOTKEEP_API_KEY=...")

    wid = os.environ.get("BOTKEEP_WORKLOAD_ID")
    if not wid:
        wls = call("GET", "/workloads")
        wid = wls["data"][0]["id"]
        print(f"使用 workload: {wid} ({wls['data'][0]['name']})")

    print("1/5 打包面板…")
    blob = build_zip()
    print(f"   zip {len(blob)//1024} KB, base64 后 {len(blob)*4//3//1024} KB")
    b64 = base64.b64encode(blob).decode()
    if len(b64) > 5_000_000:
        raise SystemExit("✗ 包太大,请清理后重试")

    print("2/5 上传 zip …")
    call("POST", f"/workloads/{wid}/files/upload",
         {"path": "/mosbox-panel.zip", "data": b64, "overwrite": True},
         idem=f"dep-up-{time.time_ns()}")
    print("   ✓ 已上传")
    print("3/5 解压到容器根目录 …")
    extracted = False
    for attempt in range(3):
        prev = call("POST", f"/workloads/{wid}/files/extract/preview",
                    {"path": "/mosbox-panel.zip", "destination": "/"},
                    idem=f"dep-pv-{time.time_ns()}-{attempt}")
        revision = prev["revision"]
        processed = [e["path"] for e in prev.get("entries", [])]
        conflicts = [e.get("path") for e in prev.get("conflicts", [])]
        print(f"   preview#{attempt}: {prev.get('files')} 文件, conflicts={len(conflicts)}")
        try:
            call("POST", f"/workloads/{wid}/files/extract",
                 {"path": "/mosbox-panel.zip", "destination": "/",
                  "revision": revision, "overwrite": True, "processed": processed},
                 idem=f"dep-ex-{time.time_ns()}-{attempt}")
            extracted = True
            break
        except SystemExit as e:
            msg = str(e)
            if "changed" in msg and attempt < 2:
                print("   revision 失效,重新 preview…")
                time.sleep(2)
                continue
            raise
    if not extracted:
        raise SystemExit("✗ 解压失败")
    # 清理 zip 包
    with contextlib.suppress(Exception):
        call("POST", f"/workloads/{wid}/files/batch",
             {"action": "delete", "paths": ["/mosbox-panel.zip"]},
             idem=f"dep-rm-{time.time_ns()}")
    print("   ✓ 已解压 (main.py / mosbox_panel/ / frontend/ / requirements.txt)")

    print("4/5 设置环境变量 BOTKEEP_PORT …")
    net = call("GET", f"/workloads/{wid}/network")
    port = net["allocations"][0]["port"]
    call("PUT", f"/workloads/{wid}/environment",
         {"key": "BOTKEEP_PORT", "value": str(port), "isSecret": False},
         idem=f"dep-env-{time.time_ns()}")
    print(f"   ✓ BOTKEEP_PORT={port}(平台分配端口)")

    print("5/5 重启容器使面板生效 …")
    try:
        call("POST", f"/workloads/{wid}/actions", {"action": "restart"},
             idem=f"dep-rs-{time.time_ns()}")
    except SystemExit:
        call("POST", f"/workloads/{wid}/actions", {"action": "start"},
             idem=f"dep-st-{time.time_ns()}")

    host = net["allocations"][0]["host"]
    print(f"""
✅ 部署完成!等待容器 running 后(约 1 分钟,首次启动会自动下载 raddy/sshd 二进制):

   面板 : http://{host}:{port}
   SSH  : ssh -p {port} root@{host}
          (默认公钥已内置,登录后可用 botkeepctl)
""")


if __name__ == "__main__":
    main()
