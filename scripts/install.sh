#!/usr/bin/env bash
# Decide-Agent 引导安装（Linux/macOS）：uv → tool install → 数据目录初始化
# 用法：curl -fsSL <raw-url>/scripts/install.sh | bash
set -euo pipefail

have() { command -v "$1" >/dev/null 2>&1; }

if ! have uv; then
  echo "[install] installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[install] installing decide-agent (PyPI)..."
uv tool install --force decide-agent || {
  echo "[install] PyPI 不可达？内网请改用: uv tool install --index-url <内网源> decide-agent"
  exit 1
}

echo "[install] 初始化数据目录与配置..."
decide-agent init || true

echo
echo "✅ 安装完成："
echo "   decide-agent demo    # 无人值守演示"
echo "   decide-agent chat    # 交互模式"
echo "   decide-agent serve-mcp --transport stdio   # MCP（Claude/opencode/codex 接入）"
echo "   decide-agent serve-http    # HTTP+SSE 服务"
echo "   decide-agent serve-a2a     # A2A 服务"
