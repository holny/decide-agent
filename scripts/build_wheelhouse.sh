#!/usr/bin/env bash
# 离线 wheelhouse 构建：全量依赖下载到 dist/wheels/，供封闭内网便携包/私服使用。
# 用法：scripts/build_wheelhouse.sh  （产物：dist/wheels/*.whl + dist/decide-agent-*.whl）
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p dist/wheels
uv build --wheel --out-dir dist
uv export --format requirements-txt --no-hashes --no-dev > dist/requirements.txt
uv pip download -r dist/requirements.txt -d dist/wheels
echo "wheelhouse ready: dist/wheels ($(ls dist/wheels | wc -l | tr -d ' ') files)"
echo "离线安装: uv pip install --no-index --find-links dist/wheels decide-agent"
