#!/bin/bash
# randoku-sidecar loopback HTTP 啟動腳本（Operator Mode + session search）
# 用法：./start.sh
# 實測寫入：RANDOKU_OPERATOR_APPLY_MODE=direct ./start.sh
#
# 開放能力（依 randoku-sidecar 實際權限模型）：
#   - read_only: 檔案讀取、cron 列出、skill 檢視
#   - cron: cron 調整
#   - skills: SKILL.md create/edit/patch/write_file/delete
#   - skills_config: config.yaml 變更
#   - workspace: 指定路徑內的檔案 patch/write（Obsidian vault, ~/Projects, ~/Downloads）
#   - memory write: 由 Operator Mode 控制，預設 dry-run
#   - memory write-back: provider 原生寫入工具（預設允許 honcho_conclude）
#   - session search: state.db FTS5 全文搜尋
# 未開放：
#   - terminal: shell 指令執行（高風險，暫不開放）
#   - owner: 任意檔案/command（需要 OWNER_ACK）

set -e

RANDOKU_DIR="${RANDOKU_DIR:-$HOME/Projects/randoku-sidecar}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
OBSIDIAN_VAULT="${OBSIDIAN_VAULT:-$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Kenzen}"
RANDOKU_LISTEN_HOST="${RANDOKU_LISTEN_HOST:-127.0.0.1}"
RANDOKU_LISTEN_PORT="${RANDOKU_LISTEN_PORT:-4750}"
PYTHON_BIN="${PYTHON_BIN:-$RANDOKU_DIR/venv/bin/python}"

cd "$RANDOKU_DIR"

# 檢查虛擬環境
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "❌ 找不到 Python interpreter：$PYTHON_BIN"
    echo "   請先建立：python3.11 -m venv venv && ./venv/bin/python -m pip install -r requirements.txt"
    exit 1
fi

# 設定 HERMES_HOME 指向主實例
export HERMES_HOME="$HERMES_HOME"

# === Operator Mode: workspace level + session search ===
export RANDOKU_OPERATOR_ENABLED="${RANDOKU_OPERATOR_ENABLED:-1}"
export RANDOKU_OPERATOR_APPLY_MODE="${RANDOKU_OPERATOR_APPLY_MODE:-dry_run}"
export RANDOKU_OPERATOR_LEVEL="${RANDOKU_OPERATOR_LEVEL:-workspace}"
export RANDOKU_OPERATOR_ALLOWED_PROFILES="${RANDOKU_OPERATOR_ALLOWED_PROFILES:-default}"
export RANDOKU_OPERATOR_ALLOWED_PATHS="${RANDOKU_OPERATOR_ALLOWED_PATHS:-$OBSIDIAN_VAULT,$HOME/Projects,$HOME/Downloads}"
# Terminal intentionally disabled by default — high risk.
export RANDOKU_ENABLE_SESSION_SEARCH="${RANDOKU_ENABLE_SESSION_SEARCH:-1}"
# Provider memory write-back allowlist (comma-separated provider-native tool
# names). Defaults to honcho_conclude here so write-back works over the
# loopback/tunnel HTTP server; override with an empty value to disable, e.g.
# RANDOKU_MEMORY_WRITEBACK_TOOLS= ./start.sh
export RANDOKU_MEMORY_WRITEBACK_TOOLS="${RANDOKU_MEMORY_WRITEBACK_TOOLS:-honcho_conclude}"
# Provider memory READ allowlist (comma-separated provider-native tool
# names), for hermes_memory_provider_read — a precise, uncached lookup
# distinct from hermes_external_context_recall's cached auto-context.
# Defaults to honcho_search (read-only: no write-capable args). Override with
# an empty value to disable, e.g. RANDOKU_MEMORY_READ_TOOLS= ./start.sh
export RANDOKU_MEMORY_READ_TOOLS="${RANDOKU_MEMORY_READ_TOOLS:-honcho_search}"

echo "🚀 啟動 randoku-sidecar MCP sidecar（loopback HTTP；Operator + session search）..."
echo "   HERMES_HOME=$HERMES_HOME"
echo "   Server: http://$RANDOKU_LISTEN_HOST:$RANDOKU_LISTEN_PORT/mcp"
echo "   Level: $RANDOKU_OPERATOR_LEVEL"
echo "   Apply mode: $RANDOKU_OPERATOR_APPLY_MODE"
echo "   Allowed profiles: $RANDOKU_OPERATOR_ALLOWED_PROFILES"
echo "   Allowed paths: $RANDOKU_OPERATOR_ALLOWED_PATHS"
echo "   Gates: session_search=$RANDOKU_ENABLE_SESSION_SEARCH, terminal=off"
echo "   Write-back tools: ${RANDOKU_MEMORY_WRITEBACK_TOOLS:-(disabled)}"
echo "   Read tools: ${RANDOKU_MEMORY_READ_TOOLS:-(disabled)}"
if [[ "$RANDOKU_OPERATOR_APPLY_MODE" == "direct" ]]; then
    echo "   WARNING: mutating tools can write when calls pass dry_run=false"
fi
echo ""

# 啟動 server（前景運行，Ctrl+C 停止）
exec "$PYTHON_BIN" "$RANDOKU_DIR/server.py" --http --host "$RANDOKU_LISTEN_HOST" --port "$RANDOKU_LISTEN_PORT" "$@"
