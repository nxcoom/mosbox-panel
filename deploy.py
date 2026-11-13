"""一键部署 MosBox Panel 到 botkeep 容器(mos-box)。

用法:
    export BOTKEEP_API_KEY="bk_live_xxx"      # 或放到上级目录 botkeep-api.txt
    python deploy.py

说明:
- 用 files/upload-batch 逐文件上传(自动创建父目录),不依赖 extract 接口
  (extract 以 "/" 为目标时和容器根目录的持续变动冲突,几乎必然 409);
- raddy / puressh sshd 二进制不在上传范围(体积原因),容器首次启动时由
  main.py 自动从 GitHub Releases 下载;若你本地已构建,可自行scp到 bin/;
- 平台写入限速:12 次/分钟,每批 4 个文件,脚本自动控制节奏。
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import contextlib

API = "https://api.botkeep.cloud/api/v1/developer"
ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".venv", "__pycache__", "data", ".git", "bin", ".zcode", "node_modules"}
SKIP_EXT = (".pyc", ".log", ".zip")
BATCH = 4                      # upload-batch 单次最多 4 个文件
WRITE_INTERVAL = 6.5           # 平台写入限速 12/min,留安全余量
PART_B64 = 3 * 1024 * 1024     # 平台单请求体上限约 3MB base64(实测),切分二进制用
BIN_NAMES = ("raddy", "sshd", "ssh-keygen")


def api_key() -> str:
    key = os.environ.get("BOTKEEP_API_KEY", "")
    if not key:
        cand = os.path.join(os.path.dirname(ROOT), "botkeep-api.txt")
        if os.path.exists(cand):
            for line in open(cand, encoding="utf-8"):
                line = line.strip()
                if line.startswith("bk_live_"):
                    key = line
                    break
    if not key:
        raise SystemExit("✗ 请先 export BOTKEEP_API_KEY=...")
    return key


def call(method, path, body=None, idem=None, retry_busy=3):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", f"Bearer {api_key()}")
    if idem:
        req.add_header("Idempotency-Key", idem)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    for attempt in range(retry_busy + 1):
        try:
            with urllib.request.urlopen(req, data=data, timeout=60) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            if e.code in (429, 503) and attempt < retry_busy:
                # 限速/忙:等待后重试
                wait = 20 * (attempt + 1)
                print(f"   ⏳ 平台限速,等待 {wait}s 后重试…")
                time.sleep(wait)
                continue
            raise SystemExit(f"✗ {method} {path} -> HTTP {e.code}: {detail}")
    raise SystemExit("✗ 重试耗尽")


def collect_files():
    out = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in sorted(files):
            if f.endswith(SKIP_EXT):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, ROOT)
            if rel.startswith(".."):
                continue
            out.append((rel, p))
    return out


def upload_one(wid: str, path: str, data_b64: str, idx: int = 0) -> bool:
    """单文件上传(分片用),返回是否成功。"""
    res = call("POST", f"/workloads/{wid}/files/upload-batch",
               {"files": [{"path": path, "data": data_b64, "overwrite": True}]},
               idem=f"dep-part-{idx:03d}-{int(time.time())}")
    saved = res.get("saved", [])
    return path in saved


def split_binaries():
    """把 bin/ 下的二进制切成 base64 ≤3MB 的分片,返回 (路径, 内容) 列表。"""
    out = []
    bin_dir = os.path.join(ROOT, "bin")
    for name in BIN_NAMES:
        p = os.path.join(bin_dir, name)
        if not os.path.exists(p):
            print(f"   ⚠ bin/{name} 不存在,跳过")
            continue
        raw = open(p, "rb").read()
        b64 = base64.b64encode(raw).decode()
        n = (len(b64) + PART_B64 - 1) // PART_B64
        for i in range(n):
            out.append((f"bkparts/{name}.part{i:02d}",
                        b64[i * PART_B64:(i + 1) * PART_B64]))
        print(f"   {name}: {len(raw) // 1024} KB → {n} 片")
    return out


def main():
    wid = os.environ.get("BOTKEEP_WORKLOAD_ID")
    with_binaries = "--with-binaries" in sys.argv
    if not wid:
        wls = call("GET", "/workloads")
        wid = wls["data"][0]["id"]
        print(f"使用 workload: {wid} ({wls['data'][0]['name']})")

    print("1/4 收集文件…")
    files = collect_files()
    total_b64 = sum(len(base64.b64encode(open(p, "rb").read())) for _, p in files)
    print(f"   {len(files)} 个文件,base64 共 {total_b64 // 1024} KB")
    if with_binaries:
        parts = split_binaries()
        print(f"   另有二进制分片 {len(parts)} 片(每片 ≤3MB base64)")
    else:
        parts = []

    print("2/4 分批上传(每批 4 个,遵守平台写入限速)…")
    for i in range(0, len(files), BATCH):
        chunk = files[i:i + BATCH]
        payload = {"files": [
            {"path": "/" + rel.replace(os.sep, "/"),
             "data": base64.b64encode(open(p, "rb").read()).decode(),
             "overwrite": True}
            for rel, p in chunk]}
        res = call("POST", f"/workloads/{wid}/files/upload-batch", payload,
                   idem=f"dep-batch-{i}-{time.time_ns()}")
        saved = res.get("saved", [])
        failed = res.get("failed", [])
        uncertain = res.get("uncertain", [])
        print(f"   [{i // BATCH + 1}/{(len(files) + BATCH - 1) // BATCH}] "
              f"✓ {len(saved)} 失败 {len(failed)} 不确定 {len(uncertain)}: "
              f"{', '.join(s.split('/')[-1] for s in saved)}")
        for f_ in failed:
            print(f"     ✗ {f_}")
        if i + BATCH < len(files):
            time.sleep(WRITE_INTERVAL)

    if parts:
        print("   清理旧分片(/bkparts 整目录,上传时自动重建)…")
        call("POST", f"/workloads/{wid}/files/batch",
             {"action": "delete", "paths": ["/bkparts"]},
             idem=f"dep-parts-clean-{time.time_ns()}")
        time.sleep(WRITE_INTERVAL)
        print("   上传二进制分片…")
        ok = 0
        for idx, (rel, b64) in enumerate(parts):
            for attempt in range(2):
                if upload_one(wid, "/" + rel, b64, idx):
                    ok += 1
                    break
                print(f"     ↻ 重试 {rel}…")
                time.sleep(15)
            else:
                print(f"     ✗ {rel} 上传失败")
            if idx + 1 < len(parts):
                time.sleep(WRITE_INTERVAL)
        print(f"   分片上传完成 {ok}/{len(parts)}")
        if ok < len(parts):
            raise SystemExit("✗ 分片上传不完整,请重新运行 python deploy.py --with-binaries")

    print("3/4 设置环境变量…")
    net = call("GET", f"/workloads/{wid}/network")
    port = net["allocations"][0]["port"]
    call("PUT", f"/workloads/{wid}/environment",
         {"key": "BOTKEEP_PORT", "value": str(port), "isSecret": False},
         idem=f"dep-env-{time.time_ns()}")
    time.sleep(WRITE_INTERVAL)
    print(f"   ✓ BOTKEEP_PORT={port}(平台分配端口)")
    if parts:
        call("PUT", f"/workloads/{wid}/environment",
             {"key": "BKP_BIN_DIR", "value": "/home/container/data/bin", "isSecret": False},
             idem=f"dep-env-bin-{time.time_ns()}")
        time.sleep(WRITE_INTERVAL)
        print("   ✓ BKP_BIN_DIR=/home/container/data/bin(拼装后的二进制落盘,重启不丢)")

    print("4/4 启动…")
    try:
        call("POST", f"/workloads/{wid}/actions", {"action": "start"},
             idem=f"dep-start-{time.time_ns()}")
    except SystemExit:
        call("POST", f"/workloads/{wid}/actions", {"action": "restart"},
             idem=f"dep-restart-{time.time_ns()}")

    host = net["allocations"][0]["host"]
    bin_note = ("二进制已随分片上传,首次启动自动拼装。"
                if parts else
                "首次启动将自动从 GitHub Releases 下载 raddy/sshd 二进制"
                "(若节点访问 GitHub 受限,请在能访问的机器上运行 python deploy.py --with-binaries)。")
    print(f"""
✅ 部署完成!等待容器 running 后(约 1 分钟。{bin_note})

   面板 : http://{host}:{port}
   SSH  : ssh -p {port} root@{host}
          (默认公钥已内置,登录后可用 botkeepctl)
""")


if __name__ == "__main__":
    main()
