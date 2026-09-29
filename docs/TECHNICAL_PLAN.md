# Decide-Agent 技术实施计划（TECHNICAL_PLAN）

> 配套 `ARCHITECTURE.md` v4。本文件是可执行的实施任务分解：P1–P6 各阶段任务、依赖、验收标准，以及 P1 的细化任务清单。
> 设计原则见 ARCHITECTURE.md 铁律 L1–L8；实施中任何与设计冲突的发现，先回写设计文档再动代码。

---

## 0. 当前基线（P0 已交付）

- 决策引擎 P0（启发式打分/合成/门控）、mock 工具、美食场景 CLI 闭环（chat/demo/init）
- 分层配置系统（defaults < 用户 config.yaml < 环境变量）、XDG 持久化目录、三级 skill 搜索链
- 测试 20 个全绿、ruff 全绿
- **注意**：P0 代码结构与 v4 目标结构的差异（见 §1.1 差异表），P1 即按差异表重构

## 0.1 P0 → v4 结构差异表（P1 重构范围）

| P0 现状 | v4 目标（v4.1 目录） | 动作 |
|---|---|---|
| `decision/`（engine/synthesizer/confidence，启发式内嵌） | `core/decision/` DecisionEngine（问题包+provider 链机制+合成/门控/沙盒；judgment 并入，不独立建模块） | 重构：启发式迁出到 experience 引擎基线规则 |
| `skills/registry.py` 内含关键词匹配 | intent 判断 = DecisionEngine.classify 问题包；加载机制归 `plugins/`（loader），场景数据外迁仓库根 `skills/` | 迁移/外迁 |
| `memory/`（in-memory stub） | 三源 ID 制（P2 做，P1 保持 stub） | 不动 |
| `orchestrator/`（session+orchestrator） | `core/workflow/`（内核：状态机/指纹/挂起/预算）+ app.entry 顶层 make_decision 入口 | 重构改名 |
| `tools/`（mock 工具+evidence） | `core/collect/`（能力注册表+DAG调度）+ 仓库根 `plugins/tools/`（内置工具插件，外迁） | 拆分/外迁 |
| 无 judgment/experience/narrator/context | 新增 experience/narrator/context 三模块 + decision 内链机制（judgment 并入 decision，recorder 归 experience） | 新建/并入 |
| `cli/` chat/demo/init | `channel/cli/` 保留 + `tools` 子命令 | 增量/迁位 |

---

## 1. P1 引擎化重构（当前阶段）

**目标**：行为与现 P0 完全等价（同一输入同一输出），但内部结构对齐 v4：单判断引擎 + provider 链骨架 + experience 引擎（P0 启发式成为其基线规则）+ workflow 内核。

### P1 任务分解

| # | 任务 | 产出 | 依赖 |
|---|---|---|---|
| P1-1 | `decision/` 链机制（judgment 并入，不独立建模块）：`base.py`（DecisionProvider 协议，含可用性自报）、`chain.py`（provider 链调度：探测+逐级降级+运行中失败降级）、`availability.py`（探测判定）；`recorder.py`（JSONL 追加，owner 分桶预留）归 `experience/` | 模块 + `schemas/error.py`（DecisionError 统一错误契约：code/severity/source/degrade_path）+ import-linter contracts（§8.7 分区）+ 单测（链降级顺序、失败注入） | 无 |
| P1-2 | `experience/` 经验引擎：`provider.py`（实现 decision.base 协议：读场景 rules.jsonc 基线 → 条件匹配 → 无命中中性分 0.5+低置信）、`recorder.py`（判断流水 JSONL）、`rules_loader.py`（加载/校验 rules.jsonc）、`converge.py`（收敛桶统计骨架，读 judgments） | 纯规则全链可跑；P0 启发式全部表达为仓库根 skills/food/rules.jsonc | P1-1 |
| P1-3 | `decision/` DecisionEngine 重构（纯逻辑，无网络 I/O）：`engine.py`（四问题包入口 + 合成器/门控/交叉验证/沙盒迁入）+ `schemas/question.py`（TypedQuestion/TypedAnswer）；`models/providers/` 判断 Provider 骨架（实现 decision.base，归适配层）：`systemone_adapter.py`（endpoint/api_key_env 可配；**P1 先不接真模型**，无 key 即 unavailable）、`llm_provider.py`（同 unavailable 骨架） | 链 `[]` 时 experience 兜底全跑通；adapter 无 key 自动跳过 | P1-1, P1-2 |
| P1-4 | `workflow/` 内核（**全同步，§8.8 并发模型**）：`state_machine.py`（沿用+input-required 态）、`fingerprint.py`（阶段指纹）、`pending.py`（PendingRequest 挂起/恢复/幂等）、`kernel.py`（make_decision 生命周期编排：意图→收集→判断→决策→呈现） | 单测：指纹续算、挂起恢复幂等 | P1-3 |
| P1-5 | `collect/` CollectScheduler：能力注册表（四类 side）、`scheduler.py`（DAG 拓扑+并行批次+降级四策略）、mock 工具外迁仓库根 `plugins/tools/` 并按能力注册（走统一插件加载器） | 美食场景收集全链走新调度器 | P1-4 |
| P1-6 | `narrator/`：模板渲染器（从现有 `_format_decision/_format_question` 迁入），`config.output.format` 支持 json_minimal/json_full/text | 三格式输出测试 | 无 |
| P1-7 | `context/` ContextEngine：最小充分集装配（按 §9 表）；DecisionEngine 的 state 由它产出 | 单测：各阶段 state 裁剪正确 | P1-3 |
| P1-8 | `config` 扩展：`decision_mode`（auto/experience）、`degrade_chain`、`provider_timeouts`、`decision_model` 段（endpoint/api_key_env，P1 仅占位）、`output.format`、`http.auth`（占位）；paths builtin 目录解析 + pyproject 打包三目录（仓库根 plugins/skills force-include → share/decide_agent/）；**离线 wheelhouse**（uv pip download 全量依赖 → wheels/，随便携包分发）+ **断网冒烟测试**（experience 模式断网全链可跑）；引导安装脚本 install.sh/ps1（装 uv → tool install → 目录初始化，可延至 P3） | 配置覆盖测试 + 打包后插件可发现 + wheelhouse 离线安装可跑 + 断网冒烟绿 | P1-3 |
| P1-9 | CLI/API 入口对齐：`channel/cli/` 走 workflow 内核（经 app.entry）；`decide-agent tools` 子命令（读能力注册表）；`serve-mcp` 子命令占位（P4.5 实做） | demo 行为与 P0 一致 | P1-4~6 |
| P1-10 | 回归与文档：全部旧测试迁移通过 + 新增链降级/指纹/挂起测试 + **P0/P1 golden-case 对拍**（同输入逐字段 diff 证明行为等价）；ARCHITECTURE 差异回写 | `uv run pytest` 全绿、ruff+import-linter 全绿、demo 实机验证 | 全部 |

### P1 验收标准（gate）

1. `uv run decide-agent demo` 输出与 P0 等价（推荐+维度分+理由+备选）
2. `decision_mode: experience` 时全程零模型调用（recorder 可查 provider=experience）
3. 人为置坏 experience（空规则）→ 链上无可用 provider → 走 missing_information 降级而非崩溃（L4）
4. 阶段指纹：重复调用相同 Evidence → 判断阶段 Skip（单测证明）
5. 挂起：PendingRequest r1 重复 respond → 幂等去重（单测）
6. 全部测试绿 + ruff 绿 + 旧测试迁移完成（删除/改写 P0 特有断言）

---

## 2. P2 记忆 ID 制

| # | 任务 | 验收 |
|---|---|---|
| P2-1 | MemoryRecord schema + MemoryStore 接口（**含 search(owner,scene,kind,query?)**）+ SQLite 存储层（WAL；evolved/users/{owner}/memory/）+ meta.json 版本迁移器（common/migrations.py） | CRUD+search 单测；迁移测试 |
| P2-2 | 三源合并（external>session>user + 冲突观察） | 合并/冲突测试 |
| P2-3 | external_memory 限额（8KB/份、32KB 总量）+ 卸载 + caveat | 超限测试 |
| P2-4 | 保鲜/淘汰（strength/half_life/归档/superseded/LRU） | 衰减与淘汰测试 |
| P2-5 | 输入驱动增删改（复用意图识别）+ 管理 API | 增删改查全流程 |
| P2-6 | owner 三层隔离 + evolved owner 优先分区落地 | 跨 owner 403 测试 |

## 3. P3 HTTP 形态

| # | 任务 | 验收 |
|---|---|---|
| P3-1 | POST /v1/decisions + decision_id 生命周期（快照持久化 runtime/，重启可恢复；SQLite WAL） | 并发决策隔离 + 重启恢复（可续/expired 结构化错误）测试 |
| P3-2 | SSE events：`schemas/events.py`（事件类型枚举+各 payload 模型）+ 包络+seq+after_seq 续传 + 挂起超时 sweeper 主动推送（core 提供同步检查方法） | 断线重连续传 + 超时 expired 推送测试 |
| P3-3 | respond 端点（PendingRequest 恢复，幂等） | GPS 零打扰交互案例 e2e |
| P3-4 | feedback 端点（**app 层编排**：memory/manager 入库落层 + weights/modulation 归因接口预留） | 反馈→learned_memory 写入 |
| P3-5 | HTTP 鉴权（config http.auth：API Key/Bearer 可配，默认关）+ owner 操作三层隔离衔接 | 未授权 401/403 + 越权隔离测试 |

## 4. P4 decision_model 接入

| # | 任务 | 验收 |
|---|---|---|
| P4-1 | `models/providers/systemone_adapter.py` 完整实现（Jev 云 + Laya 自托管，POST /v1/systemone） | 真实调用测试（有 key） |
| P4-2 | 四问题包 → 原语映射（classify=choice / score=score / extract=choice+noul / verify=noul） | 每形状单测（mock server） |
| P4-3 | 沙盒 flip 实算接入提问决策 | flip 驱动提问测试 |
| P4-4 | elicitation 增链（MCP 形态）+ decline/cancel 降级映射 | 三态处理测试 |
| P4-5 | WeightModulation 实体 + 反馈归因通道（feedback 来源）+ 影子双跑调度（experience/metrics 统一双跑执行与记录，weights 影子对照读其结果） | 权重演化测试 |

## 5. P4.5 MCP 形态

| # | 任务 | 验收 |
|---|---|---|
| P5-1 | MCP server（make_decision，stdio + Streamable HTTP，`decide-agent serve-mcp` 入口） | MCP inspector + claude-code/opencode/codex 三家宿主实测 |
| P5-2 | continuation 循环（require_action → responses） | 两轮续调 e2e |
| P5-3 | elicitation 接入（宿主声明 capability 时启用） | Claude 实测 |

## 6. P5 插件化+场景扩展

| # | 任务 | 验收 |
|---|---|---|
| P6-1 | 统一 manifest（四类 kind：scene_template/tool_server/mcp_server/**presenter**）+ 插件加载/校验/生命周期 | 非法插件跳过测试 |
| P6-2 | travel/exam/考公模板（全套 schema 文件） | 各场景 e2e |
| P6-3 | general 通用决策模板 | 用户提供候选全流程 |
| P6-4 | 意图 miss 日志 + 进化提炼脚本 | miss→模板建议流程 |

## 7. P6 A2A + 微调回流

| # | 任务 | 验收 |
|---|---|---|
| P7-1 | a2a SDK server 适配器（AgentCard 自动生成） | A2A 客户端实测 |
| P7-2 | judgments → 微调数据集导出器 | 数据集格式校验 |
| P7-3 | Laya 微调链路文档 + 自定义 checkpoint 接入 | 垂直 checkpoint 上线 |

---

## 8. 横切纪律（每阶段通用）

1. 每个 PR/提交：`uv run pytest` 全绿 + `uv run ruff check .` 全绿
2. 跨模块数据流只走 `schemas/` pydantic 模型，禁止裸 dict
3. 命名纪律：代码无 typesafe_/jev 前缀（decision_model/experience 等角色名）
4. evolved/ 是 Agent 唯一写区；测试用 tmp data_dir，不污染用户目录
5. 发现设计与实现冲突：先改 ARCHITECTURE.md（附变更说明），再改代码
6. 每阶段结束：demo 实机验证 + 测试数量/覆盖报告追加至本文档附录

---

## 附录 A. P1 引擎化重构验收报告（2026-09-24）

### A.1 Gate 六项核验

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | demo 输出与 P0 等价（推荐+维度分+理由+备选） | ✅ golden 快照测试（tests/e2e/test_p1_demo.py）；已记录差异见 A.2 |
| 2 | `decision_mode: experience` 全程零模型调用 | ✅ 集成测试 provider=experience；断网冒烟（audit hook）零外联 |
| 3 | 空规则 → 链无可用 → missing_information 降级不崩 | ✅ test_scene_without_rules_degrades_structured |
| 4 | 阶段指纹：相同 Evidence → 判断阶段 Skip | ✅ test_fingerprint_skip_on_identical_reanalysis（10/10 Skip） |
| 5 | PendingRequest 重复 respond → 幂等去重 | ✅ test_respond_idempotent_replay（原样重放） |
| 6 | 测试全绿 + ruff 绿 + 旧测试迁移 | ✅ 133 passed / ruff 0 / import-linter KEPT（P0 测试保留并对 P0 模块持续通过） |

### A.2 与 P0 输出的已记录差异（有意修正）

1. 推荐 top：辣妹子炒菜 胜出（蜀香居）——新 mock 距离为真实米数（850m）；P0 `dist_km*100` 公式致 85m 病态值
2. 理由无偏好尾巴（"符合你的偏好…"）——偏好记忆归 P2 三源记忆
3. 天气适配 0.74（雨/900m 真实插值）；P0 为 1.00（85m）

### A.3 交付结构

- 新内核：core/{decision,experience,collect,workflow,context,narrator} + judgment 并入 decision
- 适配层：models/providers（SystemOne/LLM 骨架，无 key 自动跳过）、plugins/discovery、channel/cli
- 装配：app/bootstrap（唯一全知 DI）+ app/entry（channel 唯一入口）
- 数据外置：仓库根 skills/food/{skill,dimensions,rules,prompts,info_needs}.yaml + plugins/tools/{location,weather,poi_search}
- 契约：schemas/{error,question,judgment,workflow,collect}；import-linter 五层单向
- CLI：chat/demo/init/tools + serve-mcp 占位（P4.5）

### A.4 遗留与移交

- P0 orchestrator/agents/tools 原模块保留（P0 测试仍对其通过），P2 起逐步下线
- classify 关键词兜底已实现；语义级 classify/extract/verify 归 P4（decision_model choice/noul）
- 引导安装脚本 install.sh/ps1 未做（允许延至 P3）
- 指纹粒度：score 阶段整体 payload，维度级最小重算待 P1-5 模板 dim↔input 映射正式化

---

## 附录 B. P2/P3/P4/P4.5/P6 验收报告（2026-09-24）

### B.1 P2 记忆 ID 制（25 tests）

- MemoryRecord v4 全字段 + MemoryStore 接口（search 含 scene+global 兜底）+ SQLite WAL（实测）
- 三源合并 external>session>user + 冲突观察制（挂起降权，2/2 达阈值可更新）
- external 限额 8KB/32KB 超限卸载 raw/+caveat；保鲜半衰期表 + 归档 + LRU + supersede/确认提升
- kernel 接线：respond scope=always/session → owner 层落库，outcome.learned_memory 双向
- owner 路径前缀 + 查询强制过滤双隔离；meta.json 迁移器（migrations.register_step）

### B.2 P3 HTTP 形态（含 P3-5 鉴权 + 红线 13 恢复）

- FastAPI：POST/GET /v1/decisions、respond（幂等 404/过期 409+DecisionError）、feedback（accept→completed / reject→memory）、GET/DELETE /v1/memory/{owner}、/v1/tools、/v1/capabilities
- SSE /events：包络+seq+after_seq 续传 + max_wait_s 有界流；sweeper 线程周期 expire→EXPIRED 事件主动推送
- 鉴权：X-API-Key/Bearer 可配默认关（enabled+env 缺失→500）；healthz 不设防
- 重启恢复：runtime/decisions/{id}.json（outcome+kernel snapshot）；启动重建会话，score 缓存不恢复重算即得

### B.3 P4 decision_model 传输层（P4-1/P4-2 先行）

- SystemOneAdapter 真实 POST /v1/systemone（形状→原语：classify=choice/score=score/extract=choice/verify=noul；Bearer 认证；缺 confidence 响应拒绝）
- LLMProvider OpenAI 兼容 chat/completions（JSON 强制输出、按形状 system prompt）
- httpx.MockTransport 全覆盖：解析/400→raise 链降级/密钥 probe
- **运行中失败熔断**：链内 provider 首败即禁用（进程内），后续调用零重试代价——实测修复"坏 key 每题 1.7s"的性能塌方
- 真实 Jev/Laya 契约对齐（请求/响应字段名）待拿官方 API 文档后微调 _request_payload/_parse

### B.4 P4.5 MCP 形态（1 passed e2e，官方 mcp SDK 1.x）

- `decide-agent serve-mcp`（stdio 默认 / streamable-http 可选）：make_decision 工具（首调 question→require_action；decision_id+request_id+response 续调）+ list_capabilities
- e2e：子进程 + ClientSession 真实握手→首调→续答 completed→learned_memory 双向落盘→能力清单
- ⚠️ mcp 钉 `<2`（2.x 移除 FastMCP 入口）；升级时迁移 server.py

### B.5 P6 A2A（3 passed，官方 a2a-sdk 0.3）

- `decide-agent serve-a2a`：A2AFastAPIApplication + AgentCard（/.well-known/agent-card.json）+ AgentExecutor 桥接
- message/send → Task(input-required, metadata 带 decision_id/request_id) → 同 taskId 续答 → Task(completed, artifacts=decision_report)；tasks/cancel 支持
- ⚠️ a2a-sdk 钉 `>=0.2,<1`（1.x 为 proto 模型的 v1 协议，另需迁移）

### B.6 全局验证

- **188 tests passed** / ruff 0 / import-linter 五层契约 KEPT / `uv build` 成功
- 进程级冒烟：serve-http（healthz/tools/decisions 真调用）、serve-a2a（agent-card/message-send 真调用）、serve-mcp（stdio e2e in tests）
- 依赖钉版：mcp>=1.2,<2；a2a-sdk[http-server]>=0.2,<1

---

## 附录 C. P4-3/4/5 · P5 · P6-2/3 收尾报告（2026-09-24）

### C.1 P4-3 沙盒 flip 实算

- `flip_probabilities`：缺失维度以中性 0.5×w 占位（与 kernel 一致），未知值独立 U[0,1]
- 闭式三角分布：d≤0 → p=1；d≥w → p=0；否则 (1−d/w)²/2（对称未知上限 0.5）
- kernel：p≥0.5 计入翻转风险，ask payload 携带 flip_probability

### C.2 P4-4 elicitation 增链（红线 1）

- `elicit_answers=True` 声明能力 → question 走 ctx.elicit（扁平 schema 单字段 ElicitAnswer）
- 三态：accept → 内联续答；decline/cancel → caveat+挂起保留（continuation 100% 兜底）
- elicit 通道故障 → 同兜底路径（L4）；三态单测 + 未声明能力回归

### C.3 P4-5 WeightModulation + 影子双跑

- schemas/weights.py：WeightModulation（delta/strength/half_life/source/evidence_count）
- 四层解析 resolve：clamp [基线×0.5, ×2.0] → 归一化 → EffectiveWeights 披露（"上调 xx%（来源：feedback×N）"）
- WeightModulationStore：evolved/users/{owner}/weights/modulations.json；EMA 合并 + 半衰期衰减退休
- attribute_feedback：接受/拒绝 → top-2 维度 ±0.02；HTTP feedback accept 已接线
- 影子双跑 ShadowRunner（同题全 provider 各答）+ 三指标 coverage/agreement/drift + metrics.json 落盘（达阈值→建议切经验模式，红线 9 只建议不自动）

### C.4 P5 插件化 + 场景

- 统一 manifest 四类 kind（scene_template/tool_server/mcp_server/presenter）+ 必需文件校验 + 非法跳过告警；discover_tools 接入校验
- 新模板：travel / exam / civil-service / general（全套 5 YAML）；general=用户供候选（collect 空）
- 意图 miss 日志：kernel miss_logger → evolved/users/{owner}/intent_misses.jsonl（不进全局层）

### C.5 P6-2/3 微调回流

- experience/dataset.py：judgments → SFT messages JSONL（confidence≥0.6 过滤 + 统计）
- docs/FINETUNING.md：导出 → Laya notebook → checkpoint → laya-serve endpoint 切换全链路

### C.6 依赖倒置 + P0 下线

- **import-linter 抓获真违规**：通道 import app（装配根）。重构：入口上移 `app/main.py`（pyproject script 指针）；通道只认 `DecisionAppLike/KernelLike` 协议（channel/shared/app_like.py）——契约恢复 KEPT
- P0 旧模块全下线：orchestrator/agents/tools/memory(stub)/context/skills(registry+内置数据)/decision(旧)；P0 特有测试删除（golden 快照接替其回归职责）

### C.7 最终状态

- **192 tests passed** / ruff 0 / import-linter KEPT / uv build ✅
- 进程冒烟：serve-http ✅ serve-a2a ✅ serve-mcp（stdio e2e）✅ demo/tools ✅
- 未做（显式遗留）：真实 Jev/Laya 契约对齐（需官方 API 文档）；MCP streamable-http e2e（SDK 传输层，工具逻辑已由 stdio 覆盖）；install 脚本的 PyPI 发布前提
7. 目录依赖分区由 import-linter 立约（地基 schemas/common ← core ← 适配 models/plugins/channel ← 装配 app/config；领域模块互不 import，唯一白名单：协议实现方 experience 与 models/providers → decision.base），违约 CI 红；测试目录镜像分区：tests/{unit,contract,integration,e2e}
8. 并发模型定稿：core 全同步、无网络 I/O（本地持久化只经 store/recorder 接口）；async 仅出现在 channel/app 层
9. 外部模型/引擎的确定性 stub 统一放 tests/fixtures/，不进 src
