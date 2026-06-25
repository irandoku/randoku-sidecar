#!/bin/bash
# hermes-gpt 啟動腳本（Operator Mode: workspace + memory + session search）
# 用法：./start.sh
#
# 開放能力（依 hermes-gpt 實際權限模型）：
#   - read_only: 檔案讀取、cron 列出、skill 檢視
#   - cron: cron 調整
#   - skills: SKILL.md create/edit/patch/write_file/delete
#   - skills_config: config.yaml 變更
#   - workspace: 指定路徑內的檔案 patch/write（Obsidian vault, ~/Projects, ~/Downloads）
#   - memory write: MEMORY.md / USER.md 的 add/replace/remove
#   - session search: state.db FTS5 全文搜尋
# 未開放：
#   - terminal: shell 指令執行（高風險，暫不開放）
#   - owner: 任意檔案/command（需要 OWNER_ACK）

set -e

HERMES_GPT_DIR="$HOME/Projects/randoku-sidecar"
HERMES_HOME="$HOME/.hermes"
OBSIDIAN_VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Kenzen"

cd "$HERMES_GPT_DIR"

# 檢查虛擬環境
if [[ ! -d "venv" ]]; then
    echo "❌ 找不到 venv，請先建立：python3.11 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# 啟用虛擬環境
source venv/bin/activate

# 設定 HERMES_HOME 指向主實例
export HERMES_HOME="$HERMES_HOME"

# === Operator Mode: workspace level + legacy gates ===
export HERMES_GPT_OPERATOR_ENABLED=1
export HERMES_GPT_OPERATOR_APPLY_MODE=direct
export HERMES_GPT_OPERATOR_LEVEL=workspace
export HERMES_GPT_OPERATOR_ALLOWED_PROFILES="default"
export HERMES_GPT_OPERATOR_ALLOWED_PATHS="$OBSIDIAN_VAULT,$HOME/Projects,$HOME/Downloads"
# Legacy gates (terminal intentionally disabled — high risk)
export HERMES_GPT_ENABLE_MEMORY_WRITE=1
export HERMES_GPT_ENABLE_SESSION_SEARCH=1

echo "🚀 啟動 hermes-gpt MCP sidecar（Operator: workspace + memory + session search）..."
echo "   HERMES_HOME=$HERMES_HOME"
echo "   Server: http://127.0.0.1:4750/mcp"
echo "   Level: workspace (skills + config + workspace write)"
echo "   Apply mode: direct"
echo "   Allowed profiles: default"
echo "   Allowed paths: Obsidian vault, ~/Projects, ~/Downloads"
echo "   Legacy gates: memory_write=on, session_search=on, terminal=off"
echo ""

# 啟動 server（前景運行，Ctrl+C 停止）
python server.py --http --host 127.0.0.1 --port 4750
