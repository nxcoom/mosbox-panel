# MosBox Panel

单端口复用的容器/程序管理面板 —— 在**一个端口**上同时提供 Web 管理界面与 SSH 服务,
专为 botkeep 这类只暴露单端口的容器环境设计(同样适用于任何 Linux 服务器/VPS)。

![python](https://img.shields.io/badge/python-3.11+-blue)
![raddy](https://img.shields.io/badge/前置代理-raddy_0.10-orange)
![ssh](https://img.shields.io/badge/SSH-puressh_sshd-green)

## ✨ 架构(v2.0 重写)

```
                 公网单端口 (BOTKEEP_PORT / PORT,默认 8800)
                              │
                ┌──────── raddy(L4 分流,Rust)────────┐
                │  SSH banner ──▶ puressh sshd        │
                │  WS 升级请求 ──▶ 面板 API(直通)     │
                │  其余 HTTP   ──▶ raddy HTTP 引擎    │
                └──────────────────────────────────────┘
                                     │
                     ┌───────────────┴──────────────┐
                     ▼                              ▼
               静态前端文件(raddy 直出)      /api/* /p/* /f/* ──▶ FastAPI(uvicorn)
                                                    程序守护 · R2 · 文件 · 终端
```

**2.0 的核心变化:不再手写协议嗅探与 SSH 服务。**

| 组件 | v1(旧) | v2(本版) |
|---|---|---|
| 单端口分流 | 手写 4 字节嗅探(mux.py) | **raddy** L4(SSH banner / WS regexp 分流) |
| SSH 服务 | asyncssh 手写(sshd.py,KEX bug 缠身) | **puressh sshd**(纯 Rust,完整 OpenSSH 兼容) |
| TLS/压缩 | 无 | raddy encode gzip,可选 TLS |
| 前端 | 内嵌在 Python 字符串里 | 独立静态文件,由 raddy 直出 |
| 内部端口 | 写死,端口占用即崩溃 | 启动时动态挑选空闲端口 |
| 子进程守护 | 无 | raddy / sshd 意外退出自动拉活 |

## ✨ 功能

| 功能 | 说明 |
|---|---|
| **单端口复用** | 同一端口承载 Web 面板 + SSH,raddy 按 SSH banner 自动分流 |
| **内置 SSH** | puressh sshd(纯 Rust):shell / exec / SFTP / SCP / 端口转发(-L/-R/-D),仅公钥认证,自带 `botkeepctl` 管理命令 |
| **程序守护** | 上传 .py / .zip / **二进制可执行程序**(ELF 自动识别+补执行位)即运行;崩溃自动拉活(指数退避)、开机自启、实时日志、stdin 控制台;也支持任意命令(node index.js / ./binary) |
| **分块上传** | botkeep 平台端口转发对 >16KB 的请求体丢包 —— 大文件自动按 8KB 分片上传/服务端拼装,浏览器上传不受限 |
| **Web 终端** | 浏览器里的真实 PTY shell(与 SSH 同一环境),xterm.js 本地化无 CDN 依赖 |
| **文件管理** | 在线浏览 / 编辑 / 上传 / 下载 / 重命名,per-程序目录 + 公共文件区,防路径穿越 |
| **端口发布** | 程序监听的内网端口通过 `/p/<程序ID>/<端口>/` 对外发布(HTTP + WebSocket) |
| **公共文件** | `/f/<文件>` 对外直链,支持静态站点直出 |
| **Cloudflare R2** | 备份快照上云、公共文件 R2 自定义域名发布;未配置时自动降级本地 |
| **API 密钥** | `bk_live_*` 令牌,兼容 botkeep 云平台开发者 API 风格(`/api/v1/developer/*`) |
| **安全** | scrypt 密码哈希、登录限速、会话 Cookie(HttpOnly/SameSite)、API Bearer、同源校验、sshd 仅公钥 |
| **界面** | 深色科技风 SPA,玻璃拟态 + 渐变光效,全中文,移动端适配 |

## 🚀 快速开始

```bash
# 1. 安装依赖(自动)
python main.py        # 首次启动自动 pip install + 从 GitHub 下载 raddy/sshd 二进制

# 2. 或者手动准备
pip install -r requirements.txt
# bin/ 需要两个二进制(启动时自动从 GitHub Releases 下载,也可手动放置):
#   bin/raddy     https://github.com/fokx/raddy/releases
#   bin/sshd      https://github.com/KarpelesLab/puressh/releases

# 3. 启动
BOTKEEP_PORT=8800 BOTKEEP_PASSWORD=你的密码 python main.py

# 4. 访问
#    面板: http://<ip>:8800
#    SSH : ssh -p 8800 root@<ip>   ← 同一个端口!
```

首次启动若未设置 `BOTKEEP_PASSWORD`,会生成随机密码并写入 `data/initial_password.txt`
(首次登录后自动删除)。

**默认公钥**(首次启动自动写入授权列表):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBbOngF8zJjVVvAlDWjZnGE83jXrt3aDviPCqIIE5Ju6
```

更多公钥在面板「SSH 服务」页添加,保存后自动重启 sshd 生效(已有会话不受影响)。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BOTKEEP_PORT` / `PORT` / `SERVER_PORT` | `8800` | 对外监听端口(HTTP + SSH 复用) |
| `BOTKEEP_PASSWORD` | 自动生成 | 管理密码 |
| `BOTKEEP_DATA` | `./data` | 数据目录(程序/日志/备份/SSH 密钥) |
| `BKP_RADDY_VERSION` | `v0.10.0` | 二进制自举下载的 raddy 版本 |
| `BKP_PURESSH_VERSION` | `v0.2.9` | 二进制自举下载的 puressh 版本 |
| `BKP_RADDY_URL` / `BKP_PURESSH_URL` | GitHub | 自定义二进制下载地址(内网/镜像场景) |

## 📦 部署

### 部署到新容器(一条命令)

```bash
export BOTKEEP_API_KEY="bk_live_xxx"          # 或放到上级目录 botkeep-api.txt
python deploy.py --create mybox2 --with-binaries
```

`--create` 会通过 API 创建一个新的 Python 空白容器,然后自动完成:
上传代码(含 raddy/sshd 分片)→ 设置平台分配的端口 → 启动容器。
约 1 分钟后面板就在 `http://<节点>:<端口>` 上线。

### 部署到已有容器

```bash
python deploy.py --with-binaries                          # 交互选择容器
BOTKEEP_WORKLOAD_ID=<容器id> python deploy.py --with-binaries   # 或直接指定
```

### 纯网页部署(不依赖本机脚本)

代码仓库: https://github.com/nxcoom/mosbox-panel (public)

1. botkeep 网页创建新容器(runtime: Python,source: blank)
2. 容器页 **Environment** 加环境变量 `BOTKEEP_PORT` = 平台分配的端口(Network 页可查)
3. **GitHub** 页 → Repository URL 填 `https://github.com/nxcoom/mosbox-panel`
   → Branch `main` → Check repository → 导入方式 **Replace files** → **Sync repository**
4. 启动/重启容器 —— 首次启动自动从 [Release](https://github.com/nxcoom/mosbox-panel/releases)
   下载 glibc 兼容的二进制(约 30 秒),控制台会打印初始密码

以后更新:本仓库 push 新代码后,在 botkeep 的 GitHub 页再点一次 **Sync repository** → 重启即可。

### 说明

- `deploy.py` 用平台的 `files/upload-batch` 接口逐文件上传(自动建父目录);
  **不要用 extract 接口** —— 它以 `/` 为目标时和容器根目录的持续变动冲突,必然 409。
- 二进制按 ≤3MB base64 切片放到 `bkparts/`(平台文件 API 的单请求上限),
  容器启动时由 `main.py` 自动拼装回 `bin/`。平台文件写入是异步的,
  上传完成后等 1-2 分钟再重启容器,否则拼装会拿到旧内容。
- 不带 `--with-binaries` 时,容器首次启动自动从 GitHub Releases 下载官方二进制;
  ⚠️ raddy 官方发布版要求 GLIBC ≥ 2.38,老版本容器的 python 镜像装不下,
  本仓库的分片来自 zig 交叉编译(glibc 2.31 兼容)。
- **SSH 登录用户就是 `root`**(面板「SSH 服务」页有确切命令)—— 容器以
  非 root uid(如 999)运行应用,puressh 已打补丁把习惯性的 `root` 登录名
  映射到应用自身身份:认证后的会话即应用用户,能操作面板管理的全部文件;
  容器沙箱不含真正的 root 特权(这是平台限制,任何途径都拿不到)。

## 🗂 目录结构

```
mospanel/
├── main.py                  # 入口:自举(依赖/venv/二进制)→ 编排器
├── deploy.py                # botkeep 平台一键部署
├── requirements.txt
├── bin/                     # raddy / sshd / ssh-keygen(自举下载或本地构建)
├── frontend/                # 独立静态前端(raddy 直出)
│   ├── index.html
│   ├── css/app.css
│   ├── js/app.js
│   └── js/vendor/           # xterm.js 本地化
├── mosbox_panel/
│   ├── orchestrator.py      # 编排器:挑端口/渲染配置/拉起三个进程/守护
│   ├── webapp.py            # FastAPI 全部 HTTP/WS 接口
│   ├── supervisor.py        # 程序守护(自动拉活/日志轮转/stdin)
│   ├── terminal.py          # Web 终端 PTY
│   ├── ssh.py               # 公钥管理 + sshd 控制面
│   ├── r2store.py           # Cloudflare R2
│   ├── auth.py              # scrypt + 会话 + API Key
│   ├── database.py          # SQLite 持久层
│   ├── stats.py             # psutil 采样
│   ├── events.py            # 事件总线(WS 广播)
│   ├── config.py            # 配置 + 目录布局
│   └── ctl.py               # botkeepctl 生成器
└── data/                    # 运行时数据(git 忽略)
```

## 🛠 从源码构建二进制(可选)

```bash
# raddy(含本面板使用的 WebSocket 反代补丁,上游版本亦可用 ——
# WS 会走 raddy L4 的 regexp 路由直通 uvicorn)
cd thirdparty/raddy && cargo build --release

# puressh sshd
cd thirdparty/puressh && cargo build --release --bin sshd --bin ssh-keygen -F server
```

## ⚠️ 说明

- SSH 仅允许 `root` + 公钥认证(`PasswordAuthentication no`),密钥由面板统一管理,
  存于 `data/ssh/authorized_keys`,不影响系统 `/root/.ssh/authorized_keys`。
- raddy L4 通过 SSH banner 与 WS 升级头分流;理论上若 WS 请求头被拆包到多个 TCP 段,
  该连接会落入 HTTP 引擎(打补丁的 raddy 仍能处理,上游二进制下重连即可)。
- 备份包含全部程序文件与配置;恢复前会自动停止相关程序。
