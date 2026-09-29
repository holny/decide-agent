# Decide-Agent 引导安装（Windows PowerShell 原生）
# 用法：iwr <raw-url>/scripts/install.ps1 -UseBasicParsing | iex
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "[install] installing uv..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}

Write-Host "[install] installing decide-agent (PyPI)..."
uv tool install --force decide-agent
if ($LASTEXITCODE -ne 0) {
  Write-Host "[install] PyPI 不可达？内网请改用: uv tool install --index-url <内网源> decide-agent"
  exit 1
}

Write-Host "[install] 初始化数据目录与配置..."
decide-agent init

Write-Host ""
Write-Host "✅ 安装完成："
Write-Host "   decide-agent demo    # 无人值守演示"
Write-Host "   decide-agent chat    # 交互模式"
Write-Host "   decide-agent serve-mcp --transport stdio   # MCP 接入"
Write-Host "   decide-agent serve-http    # HTTP+SSE 服务"
Write-Host "   decide-agent serve-a2a     # A2A 服务"
