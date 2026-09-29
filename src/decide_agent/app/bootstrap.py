"""Bootstrap: the ONLY all-knowing assembly module (唯一全知，ARCHITECTURE §12.2).

按分层 config 装配：provider 链（decision_model→llm→experience，无 key 自动跳过）
+ CollectScheduler（内置插件发现）+ 时钟预算 + 场景权重解析 → DecisionKernel。
测试用 build_kernel 覆盖任意组件；生产零代码改动换厂商/数据源（L3）。
"""
import json
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from decide_agent.common.log import setup as setup_logging
from decide_agent.config.loader import (
    get_confidence_params,
    get_data_dir,
    get_decision_model,
    get_llm_provider_config,
    get_stage_budgets,
    get_tools_config,
    load_config,
)
from decide_agent.config.paths import Paths, builtin_share_dirs
from decide_agent.core.collect.collector import TemplateCollector, load_info_needs
from decide_agent.core.collect.registry import CapabilityRegistry
from decide_agent.core.collect.scheduler import CollectScheduler, ToolExecutor
from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.memory.manager import MemoryService, build_memory_store
from decide_agent.core.workflow.budget import Budgets
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.experience.recorder import JudgmentRecorder
from decide_agent.models.providers.llm_provider import LLMProvider
from decide_agent.models.providers.systemone_adapter import SystemOneAdapter
from decide_agent.plugins.discovery import PluginExecutor, discover_tools
from decide_agent.schemas.collect import Capability

LOCAL_OWNER = "local"  # 单机默认 owner；多租户 identity 随 P3+ 鉴权接入


def build_memory(data_dir: str | Path | None = None) -> MemoryService:
    paths = Paths.resolve(data_dir or get_data_dir())
    return MemoryService(
        build_memory_store(paths.root / "evolved", LOCAL_OWNER),
        raw_dir=paths.root / "runtime" / "raw",
    )


def builtin_search_dirs() -> list[Path]:
    _, skills_dir = builtin_share_dirs()
    return [skills_dir]


def _discover_opencode_llm() -> dict:
    """自动发现 opencode 已配置的 LLM provider（零配置复用用户已有的 key）。

    优先级：MiniMax > deepseek > volcengine > zhipuai > opencode-go
    返回 {base_url, api_key, model} 或 {}（未找到）。
    """
    opencode_path = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    if not opencode_path.exists():
        return {}
    try:
        from decide_agent.common.jsonc import load as load_jsonc

        data = load_jsonc(opencode_path)
        providers = data.get("provider", {})
        # 按优先级试
        for name in ("MiniMax", "deepseek", "volcengine-coding-plan", "zhipuai-coding-plan", "opencode-go"):
            prov = providers.get(name)
            if not prov:
                continue
            opts = prov.get("options", {})
            base_url = opts.get("baseURL") or prov.get("api") or ""
            api_key = opts.get("apiKey") or ""
            models = list(prov.get("models", {}).keys())
            model = models[0] if models else ""
            if base_url and api_key:
                return {"base_url": base_url, "api_key": api_key, "model": model, "provider_name": name}
    except Exception:  # noqa: BLE001
        from decide_agent.common.log import get_logger as _gl
        _gl(__name__).debug("opencode LLM discovery skipped")
    return {}


def build_provider_chain(*, decision_mode: str | None = None, sink=None) -> ProviderChain:
    """Provider 链：experience 先行（免费/快）→ decision_model（必备）→ llm（可选）。

    把握度路由（chain.py）：低把握 provider 结果自动升级到下一个。
    decision_model 是通用 slot——Jev/Laya/任意 OpenAI 兼容 LLM 都可实现决策原语。
    """
    tiers: list = []

    # ① experience 先行（免费/快/确定性，有把握直接返回）
    tiers.append(ExperienceProvider(search_dirs=builtin_search_dirs()))

    # ② decision_model（必备：Jev/Laya/任意 OpenAI 兼容 LLM 均可实现决策原语）
    model_cfg = get_decision_model()
    if model_cfg.get("endpoint") and model_cfg.get("model"):
        tiers.append(SystemOneAdapter(
            endpoint=str(model_cfg["endpoint"]),
            api_key_env=model_cfg.get("api_key_env") or None,
            model=str(model_cfg["model"]),
            timeout_ms=int(model_cfg.get("timeout_ms", 5000)),
        ))

    # ③ llm 可选兜底：优先复用 opencode 已配置的 LLM（零额外配置）
    llm_cfg = get_llm_provider_config()
    if llm_cfg.get("base_url") and llm_cfg.get("model"):
        tiers.append(LLMProvider(
            api_key_env=llm_cfg.get("api_key_env") or None,
            base_url=str(llm_cfg["base_url"]),
            model=str(llm_cfg["model"]),
            timeout_ms=int(llm_cfg.get("timeout_ms", 8000)),
        ))
    elif llm_cfg.get("discover_opencode") is True:
        # opt-in：复用本机 opencode 已配置的 LLM（读取其配置文件提取 key/base_url）。
        # 默认关闭——跨应用读取凭据属敏感行为，开源环境下必须显式声明才启用。
        oc = _discover_opencode_llm()
        if oc:
            tiers.append(LLMProvider(
                base_url=oc["base_url"],
                model=oc["model"],
                api_key=oc["api_key"],
                timeout_ms=15000,
            ))

    threshold, _ = get_confidence_params()
    return ProviderChain(tiers, sink=sink, confidence_threshold=threshold)


def _build_reject_routes_loader():
    """reject_routes 装配：场景拒绝路径数据化（P8，Ontology 实践）。"""
    from functools import lru_cache

    from decide_agent.experience.reject_routes import load_reject_routes

    @lru_cache(maxsize=64)
    def loader(scene: str) -> dict:
        return load_reject_routes(scene, list(builtin_search_dirs()))

    return loader


def build_dialogue_planner(kernel: DecisionKernel, data_dir=None):
    """Agent Loop 装配（P7）：LLM 规划器 + 工具集；LLM 未配置 → (None, None) 回落状态机管道。"""
    from decide_agent.config.paths import Paths
    from decide_agent.core.agent.dialogue import AgentToolkit

    llm_cfg = get_llm_provider_config()
    base_url, model = llm_cfg.get("base_url"), llm_cfg.get("model")
    if not (base_url and model):
        return None, None
    from decide_agent.models.providers.llm_planner import LLMDialoguePlanner

    paths = Paths.resolve(data_dir or get_data_dir())
    planner = LLMDialoguePlanner(
        base_url=str(base_url), model=str(model),
        api_key_env=llm_cfg.get("api_key_env") or None,
        timeout_ms=int(llm_cfg.get("timeout_ms", 20000)),
    )
    toolkit = AgentToolkit(
        kernel=kernel,
        memory=build_memory(paths.root),
        owner_id=LOCAL_OWNER,
        experience_classify=_build_experience_classify(),
        display_resolver=resolve_dimension_display,
        candidate_generator=planner.generate_candidates,
    )
    return planner, toolkit


def _build_experience_classify():
    """经验引擎参谋：关键词先验分类（供 Agent 参考，非门槛）。"""
    from pathlib import Path as _Path

    from decide_agent.experience.provider import ExperienceProvider

    _, skills_dir = builtin_share_dirs()
    provider = ExperienceProvider(search_dirs=[_Path(skills_dir)])

    def classify(text: str) -> dict:
        from decide_agent.schemas.question import QuestionShape, TypedQuestion

        scenes = [p.name for p in _Path(skills_dir).iterdir()
                  if p.is_dir() and (p / "skill.jsonc").exists()]
        answer = provider.answer(TypedQuestion(
            shape=QuestionShape.CLASSIFY, text=text, context={"scenes": scenes},
        ))
        return {"scene": answer.value, "confidence": answer.confidence, "basis": answer.basis}

    return classify


def build_collector() -> TemplateCollector:
    plugins_dir, skills_dir = builtin_share_dirs()
    plugins = discover_tools([plugins_dir / "tools"])
    registry = CapabilityRegistry()
    for name, plugin in plugins.items():
        registry.register(Capability(name=name, side=plugin.side, description=plugin.description))
    scheduler = CollectScheduler(registry, build_tool_executor(plugins))
    return TemplateCollector(scheduler, lambda scene: load_info_needs(scene, [skills_dir]))


def build_tool_executor(plugins) -> ToolExecutor:
    """取数执行链：厂商真源（MCP/REST，缺 key 自动缺席）→ 内置 mock 兜底。"""
    from decide_agent.models.providers.poi_sources import (
        FallbackExecutor,
        build_vendor_poi_executor,
    )

    vendor = build_vendor_poi_executor(get_tools_config())
    plugin_executor = PluginExecutor(plugins)
    if vendor is None:
        return plugin_executor
    return FallbackExecutor([vendor, plugin_executor])


def list_capabilities() -> list[Capability]:
    """能力清单（decide-agent tools / GET /v1/capabilities 的数据源）。"""
    plugins_dir, _ = builtin_share_dirs()
    plugins = discover_tools([plugins_dir / "tools"])
    return sorted(
        (Capability(name=n, side=p.side, description=p.description) for n, p in plugins.items()),
        key=lambda c: c.name,
    )


def resolve_question_template(scene: str, dimension: str) -> dict | None:
    """维度 → 追问话术（prompts.jsonc questions 段）；无模板回 None（kernel 用通用话术）。"""
    _, skills_dir = builtin_share_dirs()
    path = skills_dir / scene / "prompts.jsonc"
    if not path.exists():
        return None
    from decide_agent.common.jsonc import load as load_jsonc

    data = load_jsonc(path) or {}
    return (data.get("questions") or {}).get(dimension)


def build_budgets() -> Budgets:
    limits = get_stage_budgets()  # {collect/decide/analysis: 秒}
    return Budgets(
        collect_s=limits.get("collect"),
        decide_s=limits.get("decide"),
        analysis_s=limits.get("analysis"),
    )


def resolve_scene_weights(scene: str) -> dict[str, float]:
    """场景维度权重：模板 dimensions.jsonc 为基，config weights_override 只覆盖列出的键。

    覆盖是"改数值"而非"换维度集"（否则 override 之外的维度会被丢掉不参与打分）。
    """
    _, skills_dir = builtin_share_dirs()
    path = skills_dir / scene / "dimensions.jsonc"
    weights: dict[str, float] = {}
    if path.exists():
        from decide_agent.common.jsonc import load as load_jsonc

        data = load_jsonc(path) or {}
        weights = {
            k: float(v.get("weight", 0.0))
            for k, v in (data.get("dimensions") or {}).items()
        }
    override = load_config().get("weights_override", {}).get(scene)
    if override:
        weights.update({k: float(v) for k, v in override.items()})
    return weights


def resolve_dimension_display(scene: str) -> dict[str, str]:
    _, skills_dir = builtin_share_dirs()
    path = skills_dir / scene / "dimensions.jsonc"
    if not path.exists():
        return {}
    from decide_agent.common.jsonc import load as load_jsonc

    data = load_jsonc(path) or {}
    return {k: str(v.get("display", k)) for k, v in (data.get("dimensions") or {}).items()}


def resolve_user_dims(scene: str) -> frozenset[str]:
    """模板声明需要用户输入的维度（dimensions.jsonc source: user，v4 §8）。"""
    _, skills_dir = builtin_share_dirs()
    path = skills_dir / scene / "dimensions.jsonc"
    if not path.exists():
        return frozenset()
    from decide_agent.common.jsonc import load as load_jsonc

    data = load_jsonc(path) or {}
    return frozenset(
        key for key, meta in (data.get("dimensions") or {}).items()
        if isinstance(meta, dict) and meta.get("source") == "user"
    )


def resolve_scenes() -> list[str]:
    """场景清单：内置模板目录（有 skill.jsonc 的子目录）。"""
    _, skills_dir = builtin_share_dirs()
    if not skills_dir.exists():
        return []
    return sorted(child.name for child in skills_dir.iterdir()
                  if child.is_dir() and (child / "skill.jsonc").exists())


def build_subject_memory_factory(data_dir: str | Path | None = None):
    """第三方记忆工厂：记在对象名下（evolved/users/{owner}/subjects/{subject}/memory.json）。

    仍在用户数据区内（可查看/随 owner 一起清除）；subject 做路径安全清洗。
    """
    paths = Paths.resolve(data_dir or get_data_dir())

    from decide_agent.core.memory.store import JsonMemoryStore

    def factory(subject: str) -> MemoryService:
        import re as _re

        safe = _re.sub(r"[^\w\u4e00-\u9fa5-]", "", subject) or "unnamed"
        store = JsonMemoryStore(
            paths.root / "evolved" / "users" / LOCAL_OWNER / "subjects" / safe / "memory.json",
        )
        return MemoryService(store, raw_dir=paths.root / "runtime" / "raw")

    return factory


def build_miss_logger(data_dir: str | Path | None = None) -> Callable[[dict], None]:
    """意图 miss 日志（P6-4）：users/{owner}/intent_misses.jsonl（不进全局层，红线 7）。"""
    paths = Paths.resolve(data_dir or get_data_dir())

    def log(entry: dict) -> None:
        target = paths.root / "evolved" / "users" / LOCAL_OWNER / "intent_misses.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    return log


def _load_env_file() -> None:
    """.env（gitignored）→ os.environ：密钥经 api_key_env 引用，永不落盘进配置。"""
    load_dotenv(Path.cwd() / ".env", override=False)


def build_kernel(data_dir: str | Path | None = None, *, decision_mode: str | None = None) -> DecisionKernel:
    """生产装配入口：所有具体实现只在这里出现一次。"""
    _load_env_file()
    setup_logging(load_config().get("log", {}).get("level", "WARNING"))
    paths = Paths.resolve(data_dir or get_data_dir())  # 确保数据目录就绪
    recorder = JudgmentRecorder(
        paths.root / 'evolved' / 'users', LOCAL_OWNER,
    )  # 判断流水：自进化三原料（收敛/蒸馏/微调导出）
    engine = DecisionEngine(build_provider_chain(decision_mode=decision_mode, sink=recorder))
    threshold, penalty = get_confidence_params()
    return DecisionKernel(
        engine,
        collector=build_collector(),
        budgets=build_budgets(),
        weights_resolver=resolve_scene_weights,
        question_resolver=resolve_question_template,
        memory=build_memory(data_dir),
        owner_id=LOCAL_OWNER,
        subject_memory_factory=build_subject_memory_factory(data_dir),
        miss_logger=build_miss_logger(data_dir),
        scenes=resolve_scenes(),
        user_dims_resolver=resolve_user_dims,
        gate_threshold=threshold,
        penalty_per_missing=penalty,
    )
