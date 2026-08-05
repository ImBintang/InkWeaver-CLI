#!/usr/bin/env bash
# InkWeaver 一键安装脚本（Linux / macOS，bash 3.2+）
# 用法：
#   ./install.sh                  # 默认：装依赖 + 本包，提示配置
#   ./install.sh --host qoder     # 同时写入 Qoder 的 mcp.json（claude|cursor|none）
#   ./install.sh --mirror         # pip 走清华镜像（国内网络）
#   ./install.sh --test           # 装完后做 stdio 握手验证（list_tools 打印工具数）
# 幂等：重复执行安全（.venv 存在则复用）
set -euo pipefail

HOST="none"
MIRROR=""
TEST=""
for arg in "$@"; do
    case "$arg" in
        --host) HOST="${2:-none}"; shift 2 ;;
        --host=*) HOST="${arg#*=}" ;;
        --mirror) MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple" ;;
        --test) TEST="1" ;;
        *) echo "未知参数：$arg"; exit 1 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

echo "== InkWeaver v7.2.0 一键安装 =="

# 1) 检测 Python >= 3.10
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
        PY="$cand"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "未检测到 Python 3.10+。请先安装，或用 uv：uv python install 3.11" >&2
    exit 1
fi
PYVER=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[1/5] Python $PYVER 检测通过"

# 2) 创建虚拟环境（幂等）
if [ ! -x "$VENV/bin/python" ]; then
    echo "[2/5] 创建虚拟环境 .venv ..."
    "$PY" -m venv "$VENV"
else
    echo "[2/5] .venv 已存在，复用"
fi
VENV_PY="$VENV/bin/python"
VENV_PIP="$VENV/bin/pip"

# 3) 安装依赖（uv 优先，失败回退 pip）
echo "[3/5] 安装依赖 ..."
if command -v uv >/dev/null 2>&1; then
    if ! uv pip install --python "$VENV_PY" -r "$ROOT/requirements.txt" $MIRROR; then
        echo "uv 安装失败，回退 pip ..."
        "$VENV_PIP" install $MIRROR -r "$ROOT/requirements.txt"
    fi
else
    "$VENV_PIP" install $MIRROR -r "$ROOT/requirements.txt"
fi

# 4) 安装本包
echo "[4/5] 安装 inkweaver 本包（pip install -e .）..."
"$VENV_PIP" install -e "$ROOT" $MIRROR

# 5) 写入宿主 MCP 配置
if [ "$HOST" != "none" ]; then
    echo "[5/5] 写入 $HOST 的 MCP 配置 ..."
    case "$HOST" in
        qoder)   CFG_DIR="${APPDATA:-$HOME/.config}/QoderCN/SharedClientCache"; CFG="$CFG_DIR/mcp.json" ;;
        claude)  CFG_DIR="$HOME/.config/Claude"; CFG="$CFG_DIR/claude_desktop_config.json" ;;
        cursor)  CFG_DIR="$HOME/.config/Cursor"; CFG="$CFG_DIR/mcp.json" ;;
        *) echo "未知宿主：$HOST（可选 qoder|claude|cursor|none）" >&2; exit 1 ;;
    esac
    mkdir -p "$CFG_DIR"
    if [ -f "$CFG" ]; then
        cp "$CFG" "$CFG.bak"
        echo "已存在 inkweaver 条目，备份为 $CFG.bak 后覆盖"
        JSON="$(cat "$CFG")"
        JSON="$(echo "$JSON" | python3 -c "
import json, sys
cfg = json.load(sys.stdin)
cfg.setdefault('mcpServers', {})['inkweaver'] = {
    'command': '$VENV_PY',
    'args': ['main.py', 'mcp'],
    'cwd': '$ROOT',
}
print(json.dumps(cfg, ensure_ascii=False, indent=2))
")"
        echo "$JSON" > "$CFG"
    else
        printf '{\n  "mcpServers": {\n    "inkweaver": {\n      "command": "%s",\n      "args": ["main.py", "mcp"],\n      "cwd": "%s"\n    }\n  }\n}\n' "$VENV_PY" "$ROOT" > "$CFG"
    fi
    echo "已写入 $CFG（command=$VENV_PY, cwd=$ROOT）"
else
    echo "[5/5] 跳过宿主配置写入（--host none）"
fi

# 6) 配置模板提示
if [ ! -f "$ROOT/.env/config.yaml" ]; then
    echo "提示：请复制 config.example.yaml 到 .env/config.yaml（外部编排模式可留 api_key 占位）"
fi

# 7) 握手验证
if [ -n "$TEST" ]; then
    echo "== 握手验证：启动 stdio server 并 list_tools =="
    python3 - "$ROOT" "$VENV_PY" <<'PYEOF'
import subprocess, sys, json
root, venv_py = sys.argv[1], sys.argv[2]
p = subprocess.Popen([venv_py, "main.py", "mcp"], cwd=root,
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
out = []
while True:
    line = p.stdout.readline()
    if not line: break
    obj = json.loads(line)
    if obj.get("id") == 1 and "result" in obj: out.append("initialize OK")
    if obj.get("id") == 2:
        tools = obj["result"]["tools"]; out.append(f"tools: {len(tools)}"); break
print(" | ".join(out))
PYEOF
fi

echo "== 安装完成。下一步：复制 config.example.yaml → .env/config.yaml 后接入 AI 工作台 =="
