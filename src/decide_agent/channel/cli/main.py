"""Channel CLI: chat / demo / init / tools / serve-*（马甲：只认 DecisionAppLike 协议）。

真入口在 app/main.py（组装根注入 build_app）——本模块禁 import app（§12.2）。
用户直供选项约定：问句 + 「A. 选项」行（≥2 行），如：
  周末去哪放松？
  A. 咖啡馆
  B. 书店
"""
import logging
import re
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from decide_agent.channel.shared.app_like import DecisionAppLike
from decide_agent.config.loader import get_output_language, write_user_config
from decide_agent.core.narrator.i18n import cli_strings
from decide_agent.schemas.workflow import DecisionRequest

_log = logging.getLogger(__name__)

try:
    from prompt_toolkit import prompt as _pt_prompt
except ImportError:  # pragma: no cover — prompt_toolkit 为必装依赖，此分支仅防御
    _pt_prompt = None


def _swap_recommendation(outcome) -> None:
    """「换一个」：推荐 ↔ 备选轮换（纯呈现层轮转，不重打分）。"""
    result = outcome.result
    if result is None or result.recommendation is None:
        return
    pool = [result.recommendation, *result.alternatives]
    if len(pool) < 2:
        return
    pool = pool[1:] + pool[:1]
    result.recommendation, result.alternatives = pool[0], pool[1:]


def create_cli_app(build_app: Callable[[], DecisionAppLike]) -> typer.Typer:
    app = typer.Typer(help="Decide-Agent: 帮选择困难症用户做最合适的选择")

    _MARK = re.compile(r"(?:^|\s)[A-Za-z][.、:)：]")

    def _install_sigquit_guard() -> None:
        """Ctrl+\\ 发 SIGQUIT（Python 默认不捕获 → 进程被杀 + macOS「意外退出」弹窗）→ 干净退出。

        抛 SystemExit 而非 KeyboardInterrupt：click 的 prompt 会拦截 KI 转 Abort（chat 循环
        捕不到 → 泄漏 rich traceback），且连按信号嵌套重入；SystemExit 是 BaseException，
        第三方层不碰，直接稳定退 130。
        """
        if not hasattr(signal, "SIGQUIT"):
            return

        def _exit_gracefully(*_: object) -> None:
            raise SystemExit(130)

        try:
            signal.signal(signal.SIGQUIT, _exit_gracefully)
        except (ValueError, OSError):  # 非主线程等 → 跳过（交互循环只在主线程跑）
            pass

    def _fix_tty_utf8() -> None:
        """终端行规程默认按字节擦除：CJK 退格会残留 UTF-8 孤字节（后续解码崩溃/删不动）。

        置 IUTF8 让内核 ERASE 按「字符」删；stdin 再配 errors=replace 双保险。
        """
        if not sys.stdin.isatty():
            return
        try:
            sys.stdin.reconfigure(errors="replace")
        except Exception as exc:  # noqa: BLE001 — 展示层兜底，失败不影响功能
            _log.warning("stdin reconfigure skipped: %s", exc)
        try:
            import termios

            attrs = termios.tcgetattr(sys.stdin.fileno())
            if not attrs[0] & termios.IUTF8:
                attrs[0] |= termios.IUTF8
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attrs)
        except Exception as exc:  # noqa: BLE001 — 无 termios（Windows）/非 tty → 跳过
            _log.warning("IUTF8 setup skipped: %s", exc)

    def _extract_options(text: str) -> tuple[str, list[str]] | None:
        """识别用户直供选项（≥2 个「A.」标记）→ (剩余问句, 选项)。支持多行块与单行内联。"""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # 单行内联：一行里 ≥2 个「A.」标记 → 按标记切段（前段=问句，其余=选项）
        for ln in lines:
            if len(_MARK.findall(" " + ln)) >= 2:
                parts = [seg.strip(" ；;，,") for seg in _MARK.split(" " + ln) if seg.strip()]
                if len(parts) > 2:
                    return parts[0], parts[1:]
                return "请从以下选项中帮我选择", parts
        # 多行选项块：每个标记行一条选项
        marked = [ln for ln in lines if _MARK.search(" " + ln)]
        if len(marked) >= 2:
            options = [re.sub(r"^[A-Za-z][.、:)：]\s*", "", ln) for ln in marked]
            question = " ".join(ln for ln in lines if ln not in marked).strip()
            return (question or "请从以下选项中帮我选择"), options
        return None

    def _run_turn(
        decide: DecisionAppLike, state: dict[str, Any], user_input: str,
        candidates: list[str] | None = None, render_lang: str | None = None,
    ) -> None:
        if state.get("request_id"):
            outcome = decide.respond(
                state["decision_id"], state["request_id"], {"value": user_input},
            )
            state.update(decision_id=None, request_id=None)
        else:
            parsed = _extract_options(user_input)
            if parsed is not None:
                question, parsed_opts = parsed
                candidates = (candidates or []) + parsed_opts
            else:
                question = user_input
            outcome = decide.make_decision(DecisionRequest(
                question=question, interactive=True, candidates=candidates or []))
        if outcome.status == "require_action":
            state.update(decision_id=outcome.decision_id, request_id=outcome.pending.request_id)
        elif outcome.status == "completed" and outcome.result is not None \
                and outcome.result.recommendation is not None:
            state["last_decision_id"] = outcome.decision_id  # 保留供反馈闭环 / /retry
            state["last_outcome"] = outcome  # 备选轮换（换一个）的数据源
        typer.echo(f"Decide-Agent:\n{decide.render(outcome, 'text', language=render_lang)}\n")

    @app.command()
    def chat(
        lang: str = typer.Option(None, "--lang", help="zh | en; default = config output.language"),
    ) -> None:
        """Interactive mode. 用户直供选项三种写法：
        ① 单行内联「周末去哪？ A.咖啡馆 B.书店」
        ② 选项行「A.咖啡馆」「B.书店」后再输入问句行
        ③ 先 /opt 咖啡馆,书店 再输入问句"""
        _install_sigquit_guard()
        _fix_tty_utf8()
        decide = build_app()
        render_lang = lang or get_output_language()
        if render_lang == "auto":
            render_lang = "zh"  # 首轮前默认中文；首轮后按输入检测（outcome.language）
        C = cli_strings(render_lang)
        quit_words = C["quit_words"]
        typer.echo(C["banner"])
        state: dict[str, Any] = {}
        buffer: list[str] = []
        pending_opts: list[str] = []
        while True:
            # 行编辑用 prompt_toolkit（wcwidth 感知）：终端行规程的擦除回显按字节算列，
            # CJK 宽字符占 2 列 → 退格后光标/显示错位，屏幕残留「删不掉」的已删字符。
            # 非 tty（管道/CliRunner）→ 回退 readline()（返回 "" 唯一标识 EOF）。
            if sys.stdin.isatty():
                try:
                    text = _pt_prompt(f"{C['prompt']}: ").strip()
                except (EOFError, KeyboardInterrupt):  # Ctrl+D / Ctrl+C
                    break
            else:
                typer.echo(f"{C['prompt']}: ", nl=False)
                try:
                    line = sys.stdin.readline()
                except KeyboardInterrupt:
                    break
                if line == "":
                    break
                text = line.strip()
            if text.lower() in quit_words:
                break
            if getattr(decide, "has_agent", False):
                # P7 Agent Loop：语义意图+工具编排，零枚举；规划失败 → 回落状态机管道
                try:
                    reply = decide.chat_turn(text)
                except Exception as exc:  # noqa: BLE001 — Agent 故障不阻断会话（L4）
                    _log.warning("chat_turn failed: %s", exc)
                    reply = None
                if reply is not None:
                    typer.echo(f"Decide-Agent:\n{reply}\n")
                    continue
                _log.info("agent unavailable → legacy pipeline fallback")
            if text.startswith("/whatif") and state.get("decision_id"):
                # What-If: 修改约束重新计算（如 /whatif budget=200）
                param = text[7:].strip()
                if "=" in param:
                    key, _, val = param.partition("=")
                    decide.kernel._sessions.get(state["decision_id"], None)
                    sess = decide.kernel._sessions.get(state["decision_id"])
                    if sess:
                        sess.request.slots[key.strip()] = val.strip()
                        outcome = decide.kernel._analyze(
                            state["decision_id"], sess, [])
                        typer.echo(f"Decide-Agent:\n{decide.render(outcome, 'text', language=render_lang)}\n")
                    continue
            if text == "/retry" and (state.get("decision_id") or state.get("last_decision_id")):
                outcome = decide.kernel.reset(state.get("decision_id") or state["last_decision_id"])
                state.update(decision_id=None, request_id=None)
                if outcome.status == "require_action":
                    state.update(decision_id=outcome.decision_id, request_id=outcome.pending.request_id)
                typer.echo(f"Decide-Agent:\n{decide.render(outcome, 'text', language=render_lang)}\n")
                continue
            if text.startswith("/opt"):
                pending_opts = [s.strip() for s in text[4:].split(",") if s.strip()]
                continue
            if not text:
                if buffer:
                    _run_turn(decide, state, "\n".join(buffer), candidates=pending_opts, render_lang=render_lang)
                    buffer = []
                continue
            buffer.append(text)
            is_option_line = bool(re.search(r"^\s*[A-Za-z][.、:)：]", text))
            has_pending = bool(state.get("request_id"))
            if is_option_line and not has_pending:
                continue
            # 语义意图路由（零枚举）：有对话上下文的单行输入一律链上判定——
            # 任意语言/任意说法由模型理解；挂起应答（回答追问）不在此层拦截。
            awaiting = state.pop("awaiting_feedback", False)
            contextual = awaiting or bool(state.get("last_outcome") or state.get("last_decision_id"))
            if len(buffer) == 1 and contextual and not has_pending:
                try:
                    intent = decide.kernel.entry_intent(text)
                except Exception as exc:  # noqa: BLE001 — 意图判定失败按新决策处理（L4）
                    _log.warning("entry_intent failed: %s", exc)
                    intent = None
                if intent == "swap_next" and state.get("last_outcome"):
                    buffer = []  # 语义分支已消费该行，不留入后续多行块
                    _swap_recommendation(state["last_outcome"])
                    typer.echo(f"Decide-Agent:\n{decide.render(state['last_outcome'], 'text', language=render_lang)}\n")
                    continue
                if intent == "chat":
                    from decide_agent.core.narrator.i18n import strings

                    buffer = []
                    if state.get("last_outcome"):
                        # 推荐上下文里的短困惑（"啊？"）——轻量引导，不甩闲聊长文案
                        typer.echo("对推荐有疑问或想调整，直接说（如：太贵、去过了、换一个）；/retry 推倒重来。\n")
                    else:
                        typer.echo(strings(render_lang)["chat_reply"] + "\n")
                    continue
                if intent == "dissatisfied":
                    buffer = []
                    did = state.get("last_decision_id")
                    if did:
                        # 原因可能已随本句给出（"这个我去过了"）→ 先试抽取，一轮直通；
                        # 抽不出可执行约束才反问原因
                        try:
                            outcome = decide.kernel.give_feedback(did, text)
                        except Exception as exc:  # noqa: BLE001 — 反馈回路失败退回反问（L4）
                            _log.warning("give_feedback failed: %s", exc)
                            outcome = None
                        if outcome is not None and not any(
                            line.startswith("未能从反馈中识别") for line in outcome.missing_information
                        ):
                            typer.echo(f"Decide-Agent:\n{decide.render(outcome, 'text', language=render_lang)}\n")
                            continue
                    typer.echo(
                        "哪里不满意？直接说原因（如：太贵 / 太远 / 不想去博物馆），"
                        "我按你的反馈重新推荐；回复「换一个」直接换备选；/retry 推倒重来。",
                    )
                    state["awaiting_feedback"] = True
                    continue
                if awaiting:
                    buffer = []
                    did = state.get("last_decision_id")
                    outcome = None
                    if did:
                        try:
                            outcome = decide.kernel.give_feedback(did, text)
                        except Exception as exc:  # noqa: BLE001 — 反馈回路失败退回指引（L4）
                            _log.warning("give_feedback failed: %s", exc)
                            outcome = None
                    if outcome is not None:
                        typer.echo(f"Decide-Agent:\n{decide.render(outcome, 'text', language=render_lang)}\n")
                        continue
                    typer.echo("可以这样调整：/retry 推倒重来，或直接告诉我候选（顿号分隔）。")
                    continue
                if intent == "chat":
                    from decide_agent.core.narrator.i18n import strings

                    buffer = []
                    typer.echo(strings(render_lang)["chat_reply"] + "\n")
                    continue
                if awaiting:
                    buffer = []
                    did = state.get("last_decision_id")
                    outcome = None
                    if did:
                        try:
                            outcome = decide.kernel.give_feedback(did, text)
                        except Exception as exc:  # noqa: BLE001 — 反馈回路失败退回指引（L4）
                            _log.warning("give_feedback failed: %s", exc)
                            outcome = None
                    if outcome is not None:
                        typer.echo(f"Decide-Agent:\n{decide.render(outcome, 'text', language=render_lang)}\n")
                        continue
                    typer.echo("可以这样调整：/retry 推倒重来，或直接告诉我候选（顿号分隔）。")
                    continue
                if intent == "dissatisfied":
                    buffer = []
                    typer.echo(
                        "哪里不满意？直接说原因（如：太贵 / 太远 / 不想去博物馆），"
                        "我按你的反馈重新推荐；回复「换一个」直接换备选；/retry 推倒重来。",
                    )
                    state["awaiting_feedback"] = True
                    continue
                # new_decision / None → 按新决策走管道
            block = "\n".join(buffer)
            buffer = []
            _run_turn(decide, state, block, candidates=pending_opts, render_lang=render_lang)
            pending_opts = []


    @app.command()
    def demo() -> None:
        """脚本化演示（无人值守 e2e 验证，临时目录，不污染用户数据/配置）"""
        import os
        import tempfile

        from decide_agent.config.loader import load_config

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DECIDE_AGENT_USER_CONFIG"] = str(Path(tmp) / "config.jsonc")
            load_config.cache_clear()
            try:
                decide = build_app(data_dir=Path(tmp), decision_mode="experience")
                state: dict[str, Any] = {}
                for user_input in ["我想吃饭", "想吃辣"]:
                    typer.echo(f"你: {user_input}")
                    _run_turn(decide, state, user_input, render_lang=None)
            finally:
                os.environ.pop("DECIDE_AGENT_USER_CONFIG", None)
                load_config.cache_clear()

    @app.command()
    def init() -> None:
        """生成用户配置模板（~/.config/decide-agent/config.jsonc）+ .env 模板"""
        cfg = write_user_config()
        typer.echo(f"已生成用户配置：{cfg}")

    @app.command()
    def tools() -> None:
        """列出可用能力（内置插件注册视图）"""
        for capability in build_app().list_capabilities():
            typer.echo(f"{capability.name}\t{capability.side.value}\t{capability.description}")

    @app.command("serve-mcp")
    def serve_mcp(
        transport: str = typer.Option("stdio", help="stdio | streamable-http"),
        host: str = "127.0.0.1", port: int = 8100,
    ) -> None:
        """MCP server（P4.5）：stdio（Claude/opencode/codex）或 streamable-http"""
        from decide_agent.channel.mcp_adapter.server import run as run_mcp

        run_mcp(build_app(), transport=transport, host=host, port=port)

    @app.command("serve-http")
    def serve_http(host: str = "127.0.0.1", port: int = 8000) -> None:
        """HTTP+SSE 服务（P3）：/v1/decisions | events | respond | feedback | memory"""
        import uvicorn

        from decide_agent.channel.http_adapter.app import create_http_app

        uvicorn.run(create_http_app(build_app()), host=host, port=port)

    @app.command("serve-a2a")
    def serve_a2a(host: str = "127.0.0.1", port: int = 8100) -> None:
        """A2A server（P6）：AgentCard + message/send（input-required 续答）"""
        import uvicorn

        from decide_agent.channel.a2a.server import build_a2a_app

        uvicorn.run(build_a2a_app(build_app(), url=f"http://{host}:{port}/"), host=host, port=port)

    @app.callback(invoke_without_command=True)
    def _default_to_chat(ctx: typer.Context) -> None:
        """不带子命令 = 直接进交互模式"""
        if ctx.invoked_subcommand is None:
            chat()

    return app
