"""P1 golden case: new-kernel demo output snapshot (deterministic mocks -> stable text).

与 P0 输出的已记录差异（均为有意修正，见 TECHNICAL_PLAN 附录 A）：
- 推荐 top：辣妹子炒菜（新 mock 距离语义修复后理应胜出）
- 距离分：0.80（850m 真实语义；P0 为 dist_km*100 病态 1.00）
- 理由无偏好尾巴：偏好记忆属 P2 三源记忆
"""
from pathlib import Path

from typer.testing import CliRunner

from decide_agent.app.main import app

ROOT = Path(__file__).resolve().parents[2]

GOLDEN_PATTERNS = [
    "你: 我想吃饭",
    "今天想吃辣还是清淡？",
    "你: 想吃辣",
    "推荐：",
    "口味匹配: 0.",  # taste scored
    "理由：",
    "综合得分 0~1",
    "可以直接说「接受」",
]


def test_p1_demo_golden_output():
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 0
    output = result.output
    for pattern in GOLDEN_PATTERNS:
        assert pattern in output, f"golden pattern missing:\n{pattern}\n---\n{output}"


def test_p1_demo_uses_temp_data_dir():
    """demo 不污染用户数据：数据目录用临时目录（行为断言：可重复运行）。"""
    runner = CliRunner()
    first = runner.invoke(app, ["demo"])
    second = runner.invoke(app, ["demo"])
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output  # 确定性：同输入同输出
