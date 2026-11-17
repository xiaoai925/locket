#!/data/data/com.termux/files/usr/bin/bash
# termux-setup.sh — 在手机 Termux 里一键落地 Locket 记忆引擎（phone-ai 大脑的长期记忆后端）
#
# 作用：让记忆真的存进手机、断网也能用。记忆仓是手机本地的 git 克隆，离线可存取、
# 可本地向量检索；联网时 push 到 Gitea，其它设备 pull 同步。大脑（电脑端）经 adb
# 隧道访问手机上的 locket serve-api。
#
# 用法（在手机 Termux 里）：
#   bash termux-setup.sh                      # 本地单仓，离线可用
#   LOCKET_SRC=https://<gitea>/<user>/locket-src.git bash termux-setup.sh   # 从 Gitea 拉引擎源码
#   CLOUD_URL=http://<gitea-host>:3000 CLOUD_REPO=memory bash termux-setup.sh  # 配云端同步
#   SERVE=1 bash termux-setup.sh              # 落地后顺带启动 serve-api（给大脑调）
#
# 幂等：可反复运行；已装/已配的步骤会跳过。不内置任何真实密钥——令牌请运行后用
#   `locket settings --set cloud.token=<你的令牌>` 单独配置，或在手机设置页里填。
set -euo pipefail

# ---- 可调参数（都有默认值） -------------------------------------------------
LOCKET_HOME="${LOCKET_HOME:-$HOME/.locket}"     # 记忆仓位置
LOCKET_SRC="${LOCKET_SRC:-}"                     # 引擎源码 git 地址；空=假设已在 $SRC_DIR
SRC_DIR="${SRC_DIR:-$HOME/locket-src}"           # 引擎源码存放目录
CLOUD_URL="${CLOUD_URL:-}"                        # Gitea 地址，如 http://host:3000
CLOUD_REPO="${CLOUD_REPO:-memory}"               # 云端记忆仓名
MODE="${MODE:-local}"                             # local | cloud | hybrid
EMBED="${EMBED:-local}"                           # off | local | model | cloud（local=离线向量）
PORT="${PORT:-18874}"                             # serve-api 端口；与大脑 adb reverse 的端口一致
SERVE="${SERVE:-0}"                               # 1=落地后启动 serve-api

say() { printf '\033[36m[locket]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[locket] 失败：\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 1. 依赖 ---------------------------------------------------------------
say "检查/安装依赖 python git ..."
if ! command -v python >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  pkg install -y python git || die "pkg install 失败；先 'pkg update' 或检查网络"
fi
python -c 'import sys; assert sys.version_info>=(3,10)' 2>/dev/null \
  || die "需要 Python 3.10+（Termux: pkg upgrade python）"

# ---- 2. 引擎源码 -----------------------------------------------------------
if [ -n "$LOCKET_SRC" ]; then
  if [ -d "$SRC_DIR/.git" ]; then
    say "更新引擎源码 $SRC_DIR ..."; git -C "$SRC_DIR" pull --ff-only || say "拉取失败，用现有副本"
  else
    say "克隆引擎源码 $LOCKET_SRC -> $SRC_DIR ..."; git clone "$LOCKET_SRC" "$SRC_DIR" || die "克隆引擎源码失败"
  fi
fi
[ -f "$SRC_DIR/pyproject.toml" ] || die "找不到引擎源码（设 LOCKET_SRC 从 Gitea 拉，或把 Locket 仓放到 $SRC_DIR）"

# ---- 3. 安装 locket（含本地向量所需的可选依赖按需装） -----------------------
say "安装 locket（可编辑模式）..."
pip install -e "$SRC_DIR" >/dev/null || die "pip install 失败"
# 可选：加密 secret 记忆需要 cryptography；MCP 接入需要 mcp
# pip install -e "$SRC_DIR[secret]"   # 需要时取消注释
# pip install -e "$SRC_DIR[mcp]"      # 走 MCP 对接时取消注释
command -v locket >/dev/null 2>&1 || die "locket 命令未就绪（检查 PATH / pip 安装）"

export LOCKET_HOME

# ---- 4. 初始化 / 接入云端 --------------------------------------------------
if [ -n "$CLOUD_URL" ] && [ ! -d "$LOCKET_HOME/.git" ]; then
  REMOTE="${CLOUD_URL%/}/$CLOUD_REPO.git"
  say "从云端克隆已有记忆仓 $REMOTE -> $LOCKET_HOME ..."
  if git clone "$REMOTE" "$LOCKET_HOME" 2>/dev/null; then
    say "已克隆云端记忆仓"
  else
    say "云端仓克隆失败（可能是空仓/无网/需令牌）——先本地 init，稍后再 push"
    locket init
  fi
else
  say "初始化本地记忆仓（已存在则跳过）..."
  locket init || true
fi

# ---- 5. 写入设备设置（模式 / 云端 / embedding） -----------------------------
say "配置设备设置：mode=$MODE embedding=$EMBED ..."
SET_ARGS=(--set "mode=$MODE" --set "embedding.backend=$EMBED")
[ -n "$CLOUD_URL" ] && SET_ARGS+=(--set "cloud.url=$CLOUD_URL" --set "cloud.repo=$CLOUD_REPO")
locket settings "${SET_ARGS[@]}" >/dev/null || say "settings 写入失败（可稍后在手机设置页里配）"
say "提示：令牌请单独配置，勿写进脚本：locket settings --set cloud.token=<你的Gitea令牌>"

say "完成。记忆仓：$LOCKET_HOME"
say "大脑可通过以下任一方式对接："
say "  HTTP：locket serve-api --port $PORT   （电脑端 adb reverse tcp:$PORT tcp:$PORT 后访问）"
say "  MCP ：LOCKET_HOME=$LOCKET_HOME locket mcp   （需 pip install -e \"$SRC_DIR[mcp]\"）"

# ---- 6. 可选：启动 serve-api ----------------------------------------------
if [ "$SERVE" = "1" ]; then
  say "启动 serve-api 于 0.0.0.0:$PORT（Ctrl-C 停止）..."
  exec locket serve-api --host 0.0.0.0 --port "$PORT"
fi
