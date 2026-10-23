#!/usr/bin/env bash
# knowhow 管理后台局域网启动脚本
# 用法：scripts/dev_server.sh [端口]   （默认 8000）
# 说明：绑定 0.0.0.0 使局域网内其他设备可访问；打印局域网 IP 访问地址。
set -e
PORT="${1:-8000}"

# 探测局域网 IP（macOS）
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"

cd "$(dirname "$0")/.."

echo "──────────────────────────────────────────────"
echo " knowhow 管理后台 · 局域网模式"
echo "──────────────────────────────────────────────"
echo "  本机访问:   http://localhost:${PORT}/admin"
if [ -n "$LAN_IP" ]; then
  echo "  局域网访问: http://${LAN_IP}:${PORT}/admin"
  echo "  （同一局域网内的手机/电脑浏览器输入上面地址）"
else
  echo "  局域网 IP 探测失败，请用 ifconfig 手动查本机 IP"
fi
echo "  退出: Ctrl+C"
echo "──────────────────────────────────────────────"
echo ""

# 重要：模型本地加载必须离线模式，避免反复连接 huggingface.co
HF_HUB_OFFLINE=1 exec uv run uvicorn src.api.admin_api:app --host 0.0.0.0 --port "${PORT}"
