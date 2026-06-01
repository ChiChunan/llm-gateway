#!/bin/bash
# codex_keepalive.sh
# 用 tmux 保持 codex 永久会话，定期发 /status 并捕获输出到文件
#
# 用法: ./codex_keepalive.sh [interval_seconds]
# 默认 300 秒（5分钟）

INTERVAL=${1:-300}
STATUS_FILE="/tmp/codex_status.json"
TMUX_SESSION="codex-daemon"

cleanup() {
    echo "[$(date)] 清理退出..."
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

# 如果 tmux session 已存在，先杀掉
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
sleep 1

# 创建 tmux session，启动 codex
echo "[$(date)] 创建 tmux session，启动 codex..."
tmux new-session -d -s "$TMUX_SESSION" -x 80 -y 24 "codex"

# 等待 codex 初始化
sleep 5

echo "[$(date)] codex 已启动，tmux session: $TMUX_SESSION"

# 主循环
while true; do
    # 检查 tmux session 是否还在
    if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        echo "[$(date)] tmux session 已退出，重新启动..."
        tmux new-session -d -s "$TMUX_SESSION" -x 80 -y 24 "codex"
        sleep 5
    fi

    echo "[$(date)] 发送 /status ..."

    # 清空 pane 内容，发命令
    tmux send-keys -t "$TMUX_SESSION" C-c  # 先 Ctrl+C 确保干净
    sleep 0.5
    tmux send-keys -t "$TMUX_SESSION" "/status" Enter

    # 等待输出（最多 15 秒）
    sleep 10

    # 捕获 pane 内容
    OUTPUT=$(tmux capture-pane -t "$TMUX_SESSION" -p -S -50 2>/dev/null)

    # 写入 JSON 文件
    python3 -c "
import json, sys
raw = sys.stdin.read()
data = {'timestamp': '$(date -Iseconds)', 'raw': raw}
with open('$STATUS_FILE', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
" <<< "$OUTPUT"

    echo "[$(date)] 状态已写入 $STATUS_FILE"
    sleep "$INTERVAL"
done
