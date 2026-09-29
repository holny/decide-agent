# Decide-Agent 技术实现方案（v4 · 完整定稿 · 可移植版）

> **定位**：纯决策 Agent 工具——一次调用 = 一个决策问题 = 一个场景。接收决策问题（连同外置记忆/工具/配置），执行"意图 → 收集 → 判断 → 决策 → 呈现"流水线，输出标准 JSON 决策报告。
> **不做**：会话管理、多场景对话路由、聊天、用户长期资产的组织权（记忆三源由调用方 ID 引用）。
> **四种暴露形态，一个内核**：MCP Server（业界 Agent）· HTTP+SSE（Web/微信小程序）· CLI（本地）· A2A（P6，对等 Agent）。
> 技术底座：Python 3.11+ / AgentScope 2.0 / 决策模型抽象（当前双实现：TypeSafe Jev 云端 + Laya 自托管，共用 systemone 线协议）。
> 版本：v4 综合全部设计评审（引擎收敛、经验体系、三源记忆、数据分层、插件化、交互模型、权重解析等 16 项确认清单）。与旧实现冲突时以 v4 为准重构对齐。
> v4.1（2026-09-24）目录结构修订：judgment 并入 decision（不独立建模块）、内置插件/模板数据外迁仓库根、src 内 core/ 分区，见第十二部分。
> v4.2（2026-09-24）全面复查修订：Provider 实现移出 core（归 models/providers，消除 core→适配反向依赖）、并发模型定稿（core 全同步/channel 异步）、补错误契约与事件契约、挂起超时 sweeper 与重启恢复语义、影子双跑调度落位、presenter 插件类、HTTP 鉴权与注入防护红线（见第十二/十四部分）。
> v4.3（2026-09-24）新增 12.4 分发与部署：分发四层矩阵（wheel/引导安装脚本/MCP 三家宿主接入/Docker + 便携包）、不做二进制的取舍说明、封闭内网三形态（企业内网环境）。

---

# 第一部分：产品定位与边界

- 一次 `make_decision` 调用 = 一个决策 workflow 的完整生命周期（内部可多轮挂起-应答，对外仍是同一次工具调用或同一段交互流程）。
- 对话、聊天、用户长期资产的组织归属调用方（宿主 Agent / 宿主前端）；本项目只做决策域。
- 非决策输入返回 `status: "not_a_decision"` 交还宿主。

---

# 第二部分：全局铁律（8 条）

| # | 铁律 |
|---|---|
| L1 | **模型只做判断，代码控制权重与流程**。模型只回答原子问题（"X 的口味匹配度？"→0.90）；权重分层配置、合成公式、流程编排全是代码。调优=改配置一行数字，确定性生效、单测可验、git 可审计，全程不碰 prompt |
| L2 | **选项优先+建议项标记+自由输入兜底**。有合适选项才给选项，并标记建议项（suggested，来自记忆/高置信假设，至多一个）；无合适选项绝不编造凑数；自由输入永远保留 |
| L3 | **一切可替换**：decision_model 槽位厂商无关；LLM/数据源/场景/配置全解耦；层间只传 schemas 契约 |
| L4 | **任何缺口不阻塞**：降级链+透明披露（missing_information），永不硬失败 |
| L5 | **问不问：决定性第一**（沙盒实测翻转影响）；同决定性下值得记忆的优先（rememberable 是加分乘子非门槛） |
| L6 | **scope 前置、宁可即时**：抽取时立即判作用域（once/session/always），判不清默认 once，不静默写长期记忆 |
| L7 | **输出即标准 JSON**（DecisionReport）；LLM 美化在核心之外，是可插拔呈现插件 |
| L8 | **数据四层分离**（base/evolved/runtime/user）；evolved/ 是 Agent 唯一写区 |

**L1 示例**：模型只答原子问题（口味匹配度 0.90 / 禁忌否）；权重在配置 `{taste:0.35,...}`、合成公式与流程在代码。调优=改 weights.jsonc 一行（price 0.20→0.40），不碰 prompt。

---

# 第三部分：系统架构

```
┌────────────────────────────────────────────────────────────────┐
│ 通道层 channel/（四件马甲，共享：事件总线/SSE组件/幂等恢复/seq续传）│
│  MCP(stdio|Streamable HTTP) │ HTTP(SSE+respond) │ CLI │ A2A(P6) │
├────────────────────────────────────────────────────────────────┤
│ Workflow 内核：状态机(可重入)+阶段指纹续算+挂起应答+时钟预算        │
├────────────────────────────────────────────────────────────────┤
│ DecisionEngine（决策引擎 = 判断获取 + 决策合成，全部决策职责）      │
│   问题包：classify│score│extract│verify                          │
│   Provider 链：decision_model → llm → experience（经验引擎）       │
│   决策合成：加权合成·choice交叉验证·置信门控·沙盒敏感性（纯代码）    │
├────────────────────────────────────────────────────────────────┤
│ CollectScheduler（收集调度·可选阶段·纯代码+判断外判）               │
│ narrator（呈现渲染·可选阶段·模板默认；LLM 打磨=核心外呈现插件）      │
├────────────────────────────────────────────────────────────────┤
│ 记忆（三源 ID 制）│ ContextEngine（最小充分集装配）                 │
│ 插件体系（scene_template/tool_server/mcp_server）                 │
│ 模型层 models/ │ 分层 config + 数据四层存储                        │
└────────────────────────────────────────────────────────────────┘
```

**组件职责一句话**：Workflow 管流程与状态；DecisionEngine 管全部决策；CollectScheduler 管收集执行；narrator 管呈现；chat LLM 只剩"判断②级兜底 + 抽取兜底"。

**可选阶段组件语义**（两者同性质：条件执行，由数据状态/调用方决定，非插件）：
- CollectScheduler 跳过条件：调用方自带候选 / 阶段指纹未变（只增量补采）/ 场景不需要外部信息（general 模式候选由用户提供）
- narrator 由 client `config.output.format` 决定：`json_minimal`（默认，纯结构化，不组装叙述）/ `json_full`（组装理由串+caveats，模板零 LLM）/ `text`（叠加渲染文本）

---

# 第四部分：接口设计

## 4.1 核心工具 make_decision（MCP/A2A 同源 schema）

```jsonc
inputSchema: {
  question*,                       // 用户决策问题原话
  scene_hint?,                     // 宿主判定的场景（引擎有兜底判定）
  tools[],                         // 工具白名单/内联定义（见 4.4 双语义）
  config: { ask_budget=1, allow_interactive=false,
            output: {format: "json_minimal"|"json_full"|"text"},
            weights_override?, timeouts... },
  memory: { user_memory_id?,       // 传=开启用户记忆
            session_memory_id?,    // 传=开启会话记忆（否则不建）
            external_memory: { preferences?, reference[] } }  // 限额外置参考
}
outputSchema（status 二态）:
  completed → { decision_id, recommendation{candidate,scores,reason},
    alternatives[], missing_information[], learned_memory[],
    session_memory_id?, confidence, effective_weights(可选披露) }
  require_action（仅 allow_interactive=true）→ { decision_id, actions[]:
    {request_id, kind: tool|question, input(含 options[{text,suggested?}],
     scope_choices[once|session|always]), outputSchema, timeout, budget_cost} }
```

## 4.2 各通道链路（已查证定稿）

| 通道 | 传输 | 挂起点出口 | 应答回来 |
|---|---|---|---|
| MCP | stdio（本地子进程）或 Streamable HTTP | 本轮 tool call 返回 `require_action.actions` | 宿主执行后续调 `make_decision{decision_id, responses}`（continuation 循环，无长连接——长流程被切成短请求序列，状态存 server） |
| HTTP | REST+SSE（`/v1/decisions`、`/events?after_seq=N` 断线续传） | SSE 事件 `pending_request` 实时推 | `POST /v1/decisions/{id}/respond`（request_id 幂等恢复） |
| CLI | 进程内 | 本地渲染（stdin 选项/本地工具执行） | 同进程喂回 |
| A2A(P6) | JSON-RPC；AgentCard 由 make_decision schema 生成 | Task 状态 `input-required` | 继续 SendMessage（同 contextId/taskId）；artifacts=DecisionReport |

- **双模式**：`allow_interactive=false`（默认，阻塞式）永不挂起，缺口全走静默降级进 missing_information——宿主一行接入；true 才产生 actions
- **MCP elicitation 增链**：宿主声明 elicitation capability 后，kind=question 可走标准 `elicitation/create`（仅扁平 schema，多槽位=多属性并列；accept/decline/cancel 三态，decline/cancel→降级顺延；不索敏感信息）；continuation 循环始终为 100% 兼容兜底
- **微信小程序**：原生无 SSE，`wx.request enableChunked:true + onChunkReceived` 解流（主流做法）或 WebSocket；轮询兜底
- **单段内部运算超时**：collect/decide/analysis 各设时钟预算，超时走降级链而非失败；Streamable HTTP 可发 progress 通知

## 4.3 HTTP 端点

```
POST /v1/decisions                     开启决策（返回 decision_id）
GET  /v1/decisions/{id}/events         SSE 事件流（包络 {type,decision_id,seq,payload}）
POST /v1/decisions/{id}/respond        统一应答（question 回答/tool result 同门）
POST /v1/decisions/{id}/feedback       反馈（接受/评价/记忆管理指令）
GET/DELETE /v1/memory/{owner_id}       记忆查看/删除（隐私合规）
GET  /v1/modes/experience/metrics      经验模式三指标
GET  /v1/tools                         工具发现（配置池，MCP tool 格式，可 ?scene= 过滤）
GET  /v1/capabilities                  能力清单及→工具映射（收集调度寻址视图）
CLI: decide-agent chat / demo / init / tools

鉴权：config http.auth 可配 API Key/Bearer（默认关闭，部署文档须警告）；
      owner 读写端点强制三层隔离校验（与 P2-6 衔接）。
```

## 4.4 工具来源：两路制（无独立注册端点，全部无状态）

```
❌ 无 POST /v1/client/register（注册是临时性行为，避免服务端持久注册态）

① 请求内联（临时，仅本次决策有效）
   tools: [{name, description, inputSchema, outputSchema, meta}] ← 完整定义即临时注册
   决策结束即失效，服务端零残留状态（ask_user 亦然：本次传入=本次可问人）
② 配置文件（持久定义）
   配置池：内置 tool_server / mcp_server / client 工具声明（user/ 插件与 config）

tools[] 双语义（白名单/内联合一）：
   tools: ["geo", "ask_user"]            ← 只给名字 = 白名单，引用配置池
   tools: [{...完整定义}]                 ← 内联临时注册
   可混用；最终可用集 = (配置池 ∪ 内联定义) ∩ tools[] 白名单
   tools[] 缺省 = 配置池全部可用（含 ask_user 若已配置/定义）
ask_user 启用 = 本次可用工具含 ask_user 定义 且 ask_budget>0
MCP 形态天然是模式①；HTTP 两路皆可；CLI 走配置池
```

---

# 第五部分：核心模块设计

## 5.1 DecisionEngine（决策引擎 = 判断获取 + 决策合成）

```
问题包四形状（一个 Provider 协议）：
  classify（意图：全场景概率分布）  score（候选×维度：分+概率+置信+basis）
  extract（槽位抽取：正则候选+choice 确认+noul 校验）
  verify（禁忌/矛盾/记忆校验：noul）

Provider 链（逐级可用性探测 + 运行中失败自动降）：
  ① decision_model —— SystemOneAdapter（线协议适配器，一个适配器双厂商）：
     Jev 云端 / Laya 自托管（laya-serve）共用 POST /v1/systemone 协议，
     改 endpoint 即切换。远程配置（密钥永不落盘，引用环境变量）：
       decision_model:
         adapter: systemone
         endpoint: https://your-decision-model.example.com   # 或 http://localhost:8000（自托管）
         api_key_env: TYPESAFE_API_KEY       # Laya 自托管为 LAYA_API_KEY（可无）
         model: null                          # laya checkpoint 可选
         timeout_ms: 5000
     capability flags 协商独有能力（Laya：escalate 头/hooks/router/批量）；
     可选 confidence cascade：Laya 先答（快/免费），低置信升级 Jev 复核
  ② llm —— OpenAI/Anthropic 兼容，JSON Schema 强制输出（自报置信度，仅兜底）
  ③ experience —— 经验引擎（见 5.2，永远可用）

决策合成（纯代码）：
  硬过滤（禁忌标签/预算上限×1.2）→ 加权合成（缺分维度权重置零+归一化）
  ↔ choice 交叉验证（一致加成/不一致打折/平局以模型 Choice 裁决）→ 置信门控
  沙盒敏感性测试：缺口最好/最坏值重打分 → 排序会翻转才值得问（flip_probability）；
  沙盒用快速启发式打分，只在提问决策时启用
```

## 5.2 Experience 引擎 + 价值度量

```
经验 = 先天（模板 rules.jsonc 基线）+ 后天（判断流水收敛·反馈蒸馏·规则覆盖进化）
写入：recorder JSONL（全部判断，owner 分桶；用户反馈 weight×3）
收敛：同特征桶样本≥5 且 σ<0.15 → 加权均值生效
蒸馏：离线提炼 rules_override.jsonc（可人工审，带 provenance）
影子双跑：experience/metrics 统一调度执行与记录（decision_model vs experience 同题对比），
  agreement 指标与权重影子对照通道（§5.5c）共用其结果
微调回流（Laya 路径）：标注数据集导出 → 官方 notebook 微调垂直 checkpoint
  → laya.load(自定义checkpoint) 上线 → 越用越准的飞轮（Jev 闭源无此路径）

三指标（持续维护，仅建议不自动切模式）：
  experience_coverage（规则覆盖率）/ experience_agreement（影子双跑一致率
  +用户反馈校准，反馈 weight×3）/ drift（7 天漂移）
切换建议：coverage≥0.9 ∧ agreement≥0.85 ∧ drift<0.05 → 提示可切纯经验模式
```

## 5.3 运行模式

```
decision_mode:
  auto（默认）     # decision_model → llm → experience 逐级降级
  experience       # 纯经验模式：先天基线+全部后天进化经验
                   # 离线/零模型依赖/全确定性/随经验增长变强
```

## 5.4 Workflow 内核

```
状态机：created→collecting→[asking⇄collecting]→analyzing→
        [input-required]→suggested→completed（可重入）
阶段指纹：f(输入契约哈希)——一致 Skip 复用，变化只重算该阶段及下游
挂起：PendingRequest{request_id, kind:tool|question, timeout, budget_cost}；
     respond 按 request_id 幂等恢复（重复应答去重）
时钟预算：collect/decide/analysis 各自 timeout，超时→降级链顺延（非失败）
超时扫描：通道层 sweeper 周期调用内核同步检查方法，过期→expired 事件推送+
     按超时降级；respond 时惰性校验兜底（core 保持同步纯逻辑）
挂起持久化：状态快照落 runtime/，进程重启后可恢复续答；
     不可恢复→respond 返回 expired 结构化错误（DecisionError）
```

## 5.5 权重四层解析（千人千权，L1 的个性化兑现）

```
effective_weights = resolve(scene)：
  ① 模板基线（base，全用户相同）：dimensions.jsonc
  ② 部署级覆盖（config.jsonc weights_override，运营者设定，全体生效）
  ③ owner 级画像调制（WeightModulation，evolved/users/{owner}/weights/）：
     生长通道：a.显式声明（"我在乎便宜"→strength 高）
              b.反馈学习（接受/拒绝归因到维度，P4 细化）
              c.影子对照（双跑不一致且用户采纳模型结果→折算增量）
  ④ 请求级覆盖（本次调用 config.weights_override，宿主临时指定，最高优先）
生效：逐维度 clamp [基线×0.5, 基线×2.0]（防跑飞）→ 归一化
实体：WeightModulation{owner_id, scene, dimension, delta, strength,
  half_life_days, source(declare/feedback/shadow), evidence_count, status}
  ——与 MemoryRecord 同族演化语义（衰减/矛盾确认/LRU）
保证：effective_weights 落决策轨迹可披露（"因你偏好便宜，价格权重上调 30%"）；
  owner 级不进全局层；飞轮：反馈→调制生长→推荐更准→三指标升→更少依赖模型
```

## 5.6 CollectScheduler（可选阶段，纯代码+判断外判）

```
跳过条件：调用方自带候选 / 阶段指纹未变（只增量补采）/ 场景不需外部信息
清单 = 模板 collect ∪ 意图 extra_needs ∪ 决策缺口反哺（增量补采）
能力注册表：side=server/mcp/client/memory 四类，取数优先级按注册顺序
  （典型：client GPS > server IP > memory > ask_user）
依赖 DAG（模板声明 dependencies）→ 拓扑排序 → 最大并行批次；
  失败沿依赖链归因到根节点处置
降级四策略：ask_once / degrade(缺分维度权重置零+归一化+置信扣减) /
  neutral(0.5+caveat) / block_to_clarify（唯一硬结局：候选空→转澄清）
维度↔能力映射：dimensions.jsonc source 字段；所有"动脑"点外判 DecisionEngine
  （extract/verify）——调度器自身零智能判断
```

## 5.7 narrator（可选阶段）

```
理由三层：① basis 随判断产生（代码从分数模式拼装；llm provider 同调用返回）
         ② 模板叙述（默认，确定性零成本）→ DecisionReport 纯 JSON
         ③ LLM 打磨 = 核心外可插拔呈现插件（DecisionReport → rendered），
            输入结构化 basis，输出过 failure_checks——LLM 永不参与"决定"
failure_checks：文字引用分数必须与 DecisionResult 一致，否则重试/回模板
```

---

# 第六部分：记忆子系统

```
实体：MemoryRecord{id, owner_id, scope(user/session/ephemeral), kind
  (preference/taboo/history/fact), scene, content, confidence, strength,
  half_life_days, created_at, last_used_at, used_count, source,
  status(active/pending/superseded/archived), superseded_by}
三源：user_memory_id（传=启用）/ session_memory_id（传=启用，不传不建）/
  external_memory（宿主注入；限额单份≤8KB 总量≤32KB 可配，
  超限卸载 raw_data_ref + caveat 透明披露）
合并优先级：external > session > user；冲突不静默覆盖自有条目
  （冲突观察，多次一致才更新——防宿主临时事实污染长期）
存储：user→memory.json（每 owner 一份，人类可读可编辑——用户可直接改，
  编辑即合入）+ MemoryStore 接口（P2 JSON 实现；语义检索真需要时再加索引实现，
  不动上层）；session→快照；judgments→JSONL（进化原料，append-only 机器日志）
策略：always-scope 才写长期（noul 校验，高置信才落）；保鲜 strength/half_life
  （口味~180d/预算~60d）；矛盾降权+确认（"以后都改吗？"）；
  归档/superseded/LRU 淘汰；输入驱动增删改（复用意图识别+选项确认）
隔离三层：存储路径前缀 / API 凭证 / 查询强制 owner 过滤；
  经验红线：per-owner 原始数据不进全局层（全局仅匿名聚合，人工审核）
learned_memory[] 双向：给宿主入库 + 按 scope 落自有层
隐私：GET/DELETE /v1/memory/{owner_id}
```

---

# 第七部分：数据分层（L8）

```
base/    随包发布（安装目录，升级时替换）：
         内置场景模板·内置工具声明·每模板规则基线 rules.jsonc·代码与 schema
evolved/ Agent 唯一写区，owner 优先分区（可备份可清除）：
  users/{owner_id}/                     # 每用户完整独立命名空间
    memory/        MemoryRecord
    judgments/     判断流水 JSONL
    weights/       WeightModulation
    rules/         rules_override / intent_rules（该用户经验）
    evolved_skills/ 该用户孵化的自进化模板
    datasets/      微调数据集导出
    metrics.json   该用户三指标
  global/                               # 仅匿名聚合层（严格红线：无 owner 原始数据）
    rules_consensus/ metrics.json
  → 删 users/{owner_id}=该用户完全清除；打包=完整迁移；删 evolved/=经验清零
runtime/ 易失（清了只影响进行中决策）：会话快照·cache·raw 卸载·tmp
user/    用户所有（Agent 不写）：config.jsonc·用户插件（删=回默认配置）
meta.json 数据版本 → schema 迁移；升级只替换 base，用户数据全保留
```

---

# 第八部分：插件体系

```
统一 manifest：kind: scene_template|tool_server|mcp_server|presenter
  （presenter：LLM 打磨呈现插件，DecisionReport→rendered，输出过 failure_checks，P5 落地）
搜索链：builtin < 用户 < 项目（同名覆盖）；加载即 schema 校验，非法跳过告警
生命周期：discover→validate→register→use（放目录即装/删除即卸/改JSONC即升级）

scene_template schema：
  skill.jsonc      元数据：name/display/version/author/keywords[]/requires.capabilities[]
  dimensions.jsonc dim_key: {display, description, weight, source, rubric[]}
                   （rubric 直喂 score 原语；source=能力名|user|derived）
  info_needs.jsonc collect[{capability, degrade}] + dependencies{} + follow_up
  rules.jsonc      基线规则 when/then/elif（标签+区间插值）
  prompts.jsonc    追问话术/输出格式模板
  examples/        可选：意图样例（喂 classify 提高命中率）
tool_server 插件 = 内置工具声明；mcp_server 插件 = MCP 配置（command/url+自动发现）
```

---

# 第九部分：Context 体系（每阶段最小充分集）

| 阶段 | 看到 | 看不到 |
|---|---|---|
| 意图 | question 原文、场景清单、外置记忆主题摘要 | 历史对话、候选、打分 |
| 收集 | collect 清单/DAG/降级/已有值/pending | ——（主体代码；LLM 仅缝隙） |
| 决策 | 结构化 state：候选卡片/环境/相关画像槽位/禁忌 | 自然语言对话 |
| 分析 | DecisionResult+Evidence 摘要+画像摘要+style | 原始大 JSON（只 raw_data_ref） |

压缩只发生在 workflow 循环与 raw 区；引擎调用即用即弃；状态机快照强制保留。LLM 看到的最小充分集由 ContextEngine 统一装配（唯一真源）。

---

# 第十部分：业务流程

## 10.1 定稿示例（小程序，interactive，ask_budget=2）

```
make_decision("想吃辣，预算100，最好近点", tools[geo,ask_user], memory{u123})
① 意图：classify→food(0.94)；extract→{口味:辣,预算:100,偏好:近}；三源记忆合并
② 收集：DAG 排序→weather(server)✓+geo(client)挂起 r1→小程序静默 GPS→respond 恢复
   →poi_search 8家→traffic✓→deals 失败→skip(caveat)
③ 判断：score(8×5=40 原子问题批量)+verify(禁忌)→合成临时排序
   辣妹子0.79>川味Corner0.74>蜀香居0.73；choice(整体)→top1=辣妹子→
   交叉验证一致→置信0.82；沙盒：queue 缺分可翻转 top1→生成高优先级问题
   （选项+建议项+scope_choices）→ 挂起 r2
④ 用户"不想等"(once)→指纹只重算 queue→重排→交叉验证一致→置信0.88
⑤ narrator 模板档→推荐语+备选+caveats（deals 未获取/排队估算）
⑥ DecisionReport{recommendation, alternatives, missing_information,
   learned_memory[等位敏感/once], session_memory_id, confidence}
```

## 10.2 无匹配场景四道防线

```
① 语义判断（encoder 处理"我饿了"类同义改写；关键词规则只是兜底级）
② 仍低置信→澄清（选项=场景清单+建议项标在概率最高处+「其他（请输入）」）
③ 用户输入模板外新领域→general 通用决策模式（内置模板：候选由用户提供，
   通用维度集：成本/距离/偏好匹配/风险/可行性——全流程照跑，永不说帮不了）
④ 非决策输入→status:"not_a_decision" 交还宿主
进化闭环：意图 miss 记日志→定期提炼→新场景模板的需求来源
```

## 10.3 询问机制

```
两拍制：[A拍] 阻塞性缺口（DAG 根缺失且无降级来源）收集启动即问（不空转）
       [B拍] 收集完→草稿分析→沙盒敏感性（最好/最坏值重打分）→
             排序稳定→免问（静默降级，结论鲁棒）；翻转→flip↑→提问候选
ask_score = flip_probability × rank_impact × priority_weight × rememberable_bonus
依赖：question_deps 声明保底；优先合批多槽位提问消解（MCP elicitation 扁平
schema 恰好多属性并列）；(decision_id, slot) 缓存永不重复问（附带信息即时收缩 backlog）
backlog 动态：意图后初始化→工具结果收缩→沙盒过滤→Top2 生成 head-to-head→应答收缩
ask_budget：按注册 meta 计费（ask_user=1，client 工具=0）；预算尽→静默降级同路径
```

## 10.4 哪些部分靠 LLM（职责表）

| 步骤 | 智能来源 |
|---|---|
| 意图分类 | Jev/Laya choice（语义级）；规则兜底；LLM 仅低置信/复合意图 |
| 槽位抽取 | 正则候选+decision_model 确认；脏输入升级 LLM |
| 收集调度/缺口比对/沙盒/合成/门控/指纹 | 纯代码，零模型 |
| 候选×维度打分 | decision_model score；LLM 兜底 |
| 禁忌/记忆校验/整体交叉验证 | decision_model noul/choice |
| 理由叙述 | 模板（零模型）默认；LLM 打磨=核心外插件 |
| 记忆校验（稳定偏好？） | decision_model noul |

**LLM 不可替代位仅三个**：脏输入理解兜底、叙述打磨（可选）、深度解释。

---

# 第十一部分：模型层

```
chat-strong（DeepSeek-R1/GLM/Claude：判断②级兜底、深度解释）
chat-fast（GLM-flash/MiniMax：抽取兜底）
decision_model（SystemOneAdapter 双厂商：Jev 云/Laya 自托管）
embedding（记忆检索）
统一 OpenAI/Anthropic 兼容接入；ModelRouterMiddleware 路由+degrade
```

---

# 第十二部分：目录结构（v4.1/v4.2 修订）

## 12.1 仓库三层制

```
decide-agent/                          # 仓库根：三层制
├── src/decide_agent/                  # ① 核心包：可独立发布运行的最小集
│   ├── schemas/                       # ── 地基：数据契约（pydantic，跨模块唯一语言，零内部依赖；
│   │                                  #   含 error.py 统一错误契约 / events.py 事件枚举与payload）
│   ├── common/                        # ── 地基：jsonl/clock/ids/事件总线/migrations(schema版本迁移)
│   ├── core/                          # ★ 核心域（决策业务纯逻辑：无网络 I/O，本地持久化只经接口）
│   │   ├── decision/                  #   DecisionEngine 纯逻辑：base(Provider协议：四问题包+可用性自报)
│   │   │                              #   /chain(降级调度)/availability(探测判定)
│   │   │                              #   + engine(四问题包)/synthesis/gating/sandbox
│   │   ├── experience/                #   经验引擎：provider(实现 decision.base)/recorder/收敛/蒸馏/三指标
│   │   │                              #   + 影子双跑调度(metrics)
│   │   ├── collect/                   #   能力注册表 + DAG 调度 + 降级四策略 + judge_port(判断外判协议)
│   │   ├── memory/                    #   三源合并 + Store 接口(SQLite,含search) + 保鲜淘汰 + privacy
│   │   ├── weights/                   #   权重四层解析 + WeightModulation 生长通道
│   │   ├── workflow/                  #   kernel/状态机/阶段指纹/挂起恢复/时钟预算(全同步)
│   │   ├── context/                   #   ContextEngine 最小充分集
│   │   └── narrator/                  #   模板渲染（templates/ 零 LLM）
│   ├── models/                        # ── 适配：模型接入（client_openai + router 路由降级）
│   │   └── providers/                 #   判断 Provider 实现：systemone_adapter/llm_provider
│   │                                  #   （实现 core.decision.base；外部 HTTP I/O 故归适配层）
│   ├── plugins/                       # ── 适配：插件加载机制（manifest/discovery/loader，非插件本体）
│   ├── channel/                       # ── 适配：mcp/http/cli/(a2a P6) + shared(含超时 sweeper)
│   ├── app/                           # ── 装配：bootstrap（唯一全知 DI 接线）+ entry
│   └── config/                        # ── 装配：分层配置 + paths（builtin 目录解析）
├── plugins/                           # ② 内置插件：外部信息源工具（非必要，卸载不影响内核）
│   └── tools/                         #   weather/geo/poi_search/traffic…（声明+实现，走统一加载器）
├── skills/                            # ③ 内置场景模板与默认数据：纯 JSONC 零 Python
│   ├── food/                          #   skill/dimensions/info_needs/rules/prompts/examples
│   └── general/
└── tests/ docs/ pyproject.toml        #   tests/{unit,contract,integration,e2e} + fixtures/(确定性stub)
```

**分区判据**：地基=无业务语义人人可用；core=决策业务逻辑（删任何一块产品不成立）；适配层=可替换的外部世界接口；装配层=只接线无逻辑。

## 12.2 依赖纪律（import-linter 分区立约，CI 强制）

- 分区单向（enforced 契约）：app > 适配(channel/plugins/models) > config > core > 地基(schemas/common)
  （config 独立于 app 成层：仅被上层消费、自身零业务依赖）
- 领域模块互不 import；协作只走两种方式：传 schemas 数据 / 构造注入协议
- 唯一白名单：协议实现方 → core.decision.base（experience、models/providers 两处，限单文件）
- 协议定义在消费方：DecisionProvider 在 decision/base（探测 I/O 由各 provider 实现自报，core 只做判定）；collect 判断外判经 judge_port 注入，不 import decision
- app/bootstrap.py 唯一全知（全部具体实现接线集中于此）；channel 只经 app.entry 触达内核，禁 import core 内部细节
- 跨模块数据只走 schemas/ pydantic 模型（横切纪律第 2 条）
- **并发模型定稿**：core 全同步（无网络 I/O，测试免 asyncio）；async 只出现在 channel/app 层——SSE 推送、并发决策隔离、挂起超时 sweeper 均为适配层职责

## 12.3 内置件绑定

搜索链 builtin < 用户 < 项目 不变；builtin 级目录由 config/paths.py 解析：
开发态=仓库根 plugins/、skills/；打包态 force-include → share/decide_agent/{plugins,skills}。
内置工具/模板走统一插件加载器（discover→validate→register）——放目录即装、删除即卸；
删 plugins/tools/ 内核照跑，只是没有外部信息源。

## 12.4 分发与部署

**不做二进制编译**（PyInstaller/Nuitka）：插件体系靠运行时动态 import 仓库 plugins/ 下的
Python 工具，二进制冻结构建期依赖清单 → 新插件遇未预置依赖即 ImportError 且无法补装，
插件退化为"只能装构建时预知依赖"；且本产品无 GUI，四种形态均假设有 uv/容器环境。
"零安装"体验由服务形态（④）与引导脚本（②）实现，不靠打包形态。

| 层 | 形态 | 适用 |
|---|---|---|
| ① PyPI wheel | `uvx` / `pip install decide-agent`（console_scripts 入口） | 有 Python 环境者 |
| ② 引导安装脚本 | `curl …/install.sh \| bash`（Windows install.ps1）：装 uv → uv tool install → 数据目录初始化 → `decide-agent` 即用（对齐 hermes-agent 的 managed install 模式） | 本地 CLI/无 Python 用户 |
| ③ MCP 宿主配置 | `uvx decide-agent serve-mcp` 一行接入 | claude-code / opencode / codex 等 MCP 宿主 |
| ④ Docker 镜像 | HTTP/A2A 服务端；`docker save/load` 离线入内网；evolved//runtime/ 挂卷，升级换镜像不丢数据 | 企业内网/服务器部署 |
| ⑤ 便携包（可选） | 嵌入式 Python（python-build-standalone）+ wheels 全量，解压双击 start.bat/sh | demo/试用、Windows 桌面 |

**MCP 宿主接入**（stdio，P4.5 提供 `decide-agent serve-mcp` 子命令）：
- claude-code：`claude mcp add decide-agent -- uvx decide-agent serve-mcp`
- opencode：opencode.json `{ "mcp": { "decide-agent": { "type": "local", "command": ["uvx","decide-agent","serve-mcp"] } } }`
- codex：`~/.codex/config.toml` `[mcp_servers.decide-agent]` `command="uvx"` `args=["decide-agent","serve-mcp"]`
- 宿主无 MCP 时兜底：agent 直接 shell 调 `decide-agent ask "…"`（单轮阻塞 + json_minimal，机器友好，§4.2 双模式承诺）

**封闭内网（企业内网环境）三形态**：
- 桌面便携包：离线 zip（嵌入式 Python + wheels/ 全量依赖），解压即装，用户级无需管理员
- 内网私服 wheel：Nexus/Artifactory `pip install --index-url 内网源`，附 SBOM 依赖清单过安全审批
- Docker 离线镜像：save → 拷入 → load

**内网运行时闭环**（零外联）：
- 模型端点接企业大模型平台（OpenAI 兼容网关）；decision_model 可接自托管推理服务或不配
- experience 模式零模型依赖兜底（最坏情况全链可跑）；可用性探测失败自动沉到 experience 链（L4 降级链即离线路径）
- 工具换企业内网数据源（同插件机制注册）；MCP stdio 全离线可用
- 数据不出内网：SQLite/JSONL/evolved/ 全本地，无遥测；CI 断网冒烟测试保证启动零外联

## 12.5 ask_user 归属

问的语义（ask_score/ask_budget/挂起恢复/降级）在核心（workflow+collect+sandbox）；
投递实现随通道（CLI=stdin 选项 / MCP=elicitation 或 require_action / HTTP=pending_request+respond），不作插件；
能力注册表照常注册 side=client、ask_budget=1 计费、tools[] 白名单过滤（§10.3 语义不变）。

---

# 第十三部分：分期实施计划

| 阶段 | 任务 | 验收 |
|---|---|---|
| **P1 引擎化重构**（起点） | decision 链机制(base/chain/availability，judgment 并入不独立建模块)、DecisionEngine 四问题包骨架、experience 引擎（recorder 归入；迁移 P0 启发式为 rules.jsonc 基线）、workflow 收编、CollectScheduler 独立、narrator 模板化 | 行为与现 P0 等价；`decision_mode: experience` 全链可跑；测试全绿 |
| **P2 记忆 ID 制** | MemoryRecord+SQLite、三源合并、限额、保鲜/淘汰、管理 API、owner 三层隔离、evolved owner 优先分区 | ID 开关均可用；隔离/淘汰测试 |
| **P3 HTTP 形态** | decisions/events/respond + PendingRequest 全链 + after_seq 续传 + 鉴权可配 + 重启恢复 + 超时推送 | 模拟前端 GPS 零打扰交互案例 |
| **P4 decision_model 接入** | models/providers SystemOneAdapter 双厂商 + 沙盒 flip 实算 + elicitation 增链 + WeightModulation 反馈通道 + 影子双跑调度 | degrade_chain 逐级实测；Laya 自托管无 key 可跑 |
| **P4.5 MCP 形态** | make_decision + continuation 循环 + learned_memory 输出 | Claude 等宿主实测 |
| **P5 插件化+场景扩展** | 统一 manifest（含 presenter 呈现插件）+ travel/exam/考公 + general 模板 + 意图 miss 日志 | 新场景/新工具零 Python |
| **P6 A2A + 微调回流** | a2a SDK server 适配 + 数据集导出 + Laya 微调链路 | A2A 客户端实测；垂直 checkpoint 上线 |

---

# 第十四部分：重点注意事项（红线）

1. elicitation 仅扁平 schema（合批=多属性并列）；decline/cancel→降级顺延；不索敏感信息
2. AgentScope A2A 仅 client 侧；server 用官方 a2a SDK（`pip install "agentscope[a2a]"`）
3. decision_model 命名纪律：代码无 typesafe_/jev 前缀；厂商名只在 provider 实现与 config
4. 阻塞式默认单轮零挂起、缺口全披露（宿主一行接入承诺）
5. decision_id/request_id 双键幂等；client 工具 outputSchema 硬校验（失败=故障同级降链）
6. LLM 永不参与"决定"，只参与兜底判断与核心外说话
7. evolved/ 唯一写区、owner 优先分区；per-owner 数据不进全局层
8. 外置记忆强制限额；POI 等大结果 raw_data_ref 卸载
9. 模式切换永远只建议不自动；三指标持续维护
10. 沙盒用快速启发式打分，只在提问决策时启用
11. 微信小程序流式走 enableChunked（原生无 SSE）或 WebSocket
12. 记忆学习防污染：scope 判定前置；冲突观察制；反馈权重×3；低置信挂起
13. 挂起态决策快照存 runtime/，进程重启可恢复续答；不可恢复/已超时 → respond 返回 expired 结构化错误（DecisionError），宿主侧重发新决策
14. 外部注入内容（external_memory/工具返回值）只进结构化槽位（ContextEngine 装配的 state 字段），永不原样拼接进 LLM prompt 自由文本区
15. HTTP 通道鉴权可配（API Key/Bearer，默认关闭须部署文档警告）；memory 读写端点强制 owner 校验与三层隔离

---

# 第十五部分：研究附录（查证依据）

- **TypeSafe Jev**：System One 决策模型（choice/score/noul 三原语；state+类型化问题→结构化答案+校准置信；不做文本生成）；云端 API key 接入
- **Laya**：开源同类（Apache 2.0，33ms 单问/7.2ms 批量，100+ 语言 Router，Act/Escalate 头，prediction hooks，微调 notebooks，可选 MCP server）；**laya-serve 与 Jev 线协议兼容（同 POST /v1/systemone）**→ 一个适配器双厂商
- **MCP elicitation（2025-06）**：server→client 反向收集用户输入的标准通道；扁平 schema 限制+三态应答+安全条款
- **AgentScope 2.0**：ReAct/Toolkit/Context/Middleware/Agent Service；`A2AAgent` 仅 client 侧，server 用官方 a2a SDK
- **微信小程序**：原生无 SSE；enableChunked 分块解流为主流方案或 WebSocket
