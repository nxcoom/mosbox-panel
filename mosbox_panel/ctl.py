"""botkeepctl —— SSH 会话内控制面板的命令行工具(通过本机回环 HTTP)。"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

TEMPLATE = r'''#!/usr/bin/env python3
"""botkeepctl — {app_name} 控制命令
  status            面板总览
  ps                运行中的程序
  start <名称|id>   启动程序
  stop  <名称|id>   停止程序
  restart <名称|id> 重启程序
  logs  <名称|id> [行数]   查看最近日志(默认 50 行)
  help              帮助
"""
import json, os, sys, urllib.request

PANEL = os.environ.get("BKP_PANEL", "{panel_url}")
TOKEN = os.environ.get("BKP_TOKEN", "")

def call(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PANEL + path, data=data, method="POST" if data else "GET")
    req.add_header("X-BKP-Token", TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

def find_wl(name):
    data = call("/api/ctl/ps")
    for w in data["workloads"]:
        if w["name"] == name or w["id"] == name:
            return w
    for w in data["workloads"]:
        if name.lower() in w["name"].lower():
            return w
    return None

def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    try:
        if cmd in ("status", "ps", "ls"):
            data = call("/api/ctl/ps")
            sys_st = data.get("system", {{}})
            print(f"面板: {{'运行中 ' + str(data.get('running_count', 0))}}/{{len(data.get('workloads', []))}} 个程序"
                  f"   CPU {{sys_st.get('cpu_percent', 0)}}%   内存 {{sys_st.get('mem_percent', 0)}}%")
            print(f"{{'名称':<20}} {{'状态':<10}} {{'PID':<8}} 重启  自动拉活")
            for w in data.get("workloads", []):
                print(f"{{w['name']:<20}} {{w['status']:<10}} {{str(w.get('pid') or '-'):<8}}"
                      f" {{w.get('restarts', 0):<5}} {{'是' if w.get('auto_restart') else '否'}}")
        elif cmd in ("start", "stop", "restart"):
            w = find_wl(args[1]) if len(args) > 1 else None
            if not w:
                print(f"找不到程序: {{args[1] if len(args) > 1 else ''}}"); sys.exit(1)
            r = call(f"/api/ctl/{{cmd}}", {{"id": w["id"]}})
            print(("✓ " if r.get("ok") else "✗ ") + f"{{cmd}} {{w['name']}}"
                  + (f" — {{r['detail']}}" if r.get("detail") else ""))
        elif cmd == "logs":
            w = find_wl(args[1]) if len(args) > 1 else None
            if not w:
                print("找不到程序"); sys.exit(1)
            lines = int(args[2]) if len(args) > 2 else 50
            data = call(f"/api/ctl/logs?id={{w['id']}}&lines={{lines}}")
            print("\n".join(data.get("lines", [])))
        elif cmd in ("help", "-h", "--help"):
            print(__doc__)
        elif cmd == "status-detail":
            print(json.dumps(call("/api/ctl/ps"), indent=2, ensure_ascii=False))
        else:
            print(f"未知命令 {{cmd}},输入 botkeepctl help 查看帮助"); sys.exit(1)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", "")
        except Exception:
            detail = e.reason
        print(f"✗ {{detail or e}}"); sys.exit(1)
    except urllib.error.URLError as e:
        print(f"✗ 无法连接面板 ({{PANEL}}): {{e.reason}}"); sys.exit(1)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
'''


def write_botkeepctl(bin_dir, panel_url: str, token_file) -> str:
    """生成可执行的 botkeepctl 脚本,返回路径。SSH 会话 PATH 已包含 bin_dir。"""
    import os
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "botkeepctl"
    from . import APP_NAME
    script = TEMPLATE.format(app_name=APP_NAME, panel_url=panel_url)
    path.write_text(script, encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)
