# Laya 微调回流链路（P6-2/P6-3）

## 链路总览

```
judgments JSONL（recorder 落盘，owner 分桶）
  → decide-agent.dataset.export_dataset（本包 experience/dataset.py）
  → SFT 数据集（messages 格式 JSONL）
  → Laya 官方 notebook 微调 → 垂直 checkpoint
  → config decision_model.endpoint 指向 laya-serve（同 POST /v1/systemone 协议）
  → 上线（Jev 云端不受影响；越用越准的飞轮，v4 §5.2）
```

## 导出

```bash
uv run python -c "
from pathlib import Path
from decide_agent.experience.dataset import export_dataset
print(export_dataset(
    [Path('~/.local/share/decide-agent/evolved/users/local/judgments.jsonl').expanduser()],
    Path('dist/sft_food.jsonl'),
))
"
```

- 只导出 `outcome=ok`、`shape=score`、数值答案、`confidence ≥ 0.6` 的判定（可配阈值）
- system prompt 与四问题包信封保持一致——微调后的 checkpoint 直接服务 `decision_model` 槽位

## Laya 侧

1. 用 Laya 官方微调 notebook 加载 `sft_food.jsonl`（messages 格式原生兼容）
2. 产出垂直 checkpoint → `laya-serve` 启动（默认 `http://localhost:8000`）
3. 配置切换（零代码，L3）：

```yaml
decision_model:
  endpoint: http://localhost:8000
  api_key_env: null          # 自托管无需密钥
  model: food-vertical-v1
```

## 红线提醒

- 微调数据源自 `evolved/users/{owner}/judgments.jsonl`——owner 分桶导出，**禁止合并进全局层**（红线 7）
- 导出数据集含用户决策原始问题：对外共享前需匿名化审查
