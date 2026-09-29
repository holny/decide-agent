# 配置说明（CONFIG）

决定一切的优先级：`内置 defaults.jsonc < 用户 ~/.config/decide-agent/config.jsonc < 环境变量 DECIDE_AGENT__段__键=值`。
配置格式为 **JSONC**（支持注释的 JSON）；未配置的 provider 层自动不可用，链降级到下一层（L4）。

## decision

| 键 | 默认 | 说明 |
|---|---|---|
| confidence_threshold | 0.65 | 置信门控：低于则追问（interactive 时） |
| confidence_penalty_per_missing | 0.40 | 每个无法评分维度的置信扣减 |
| decision_mode | auto | `auto`（provider 链逐级降级）\| `experience`（纯规则零模型，内网可用） |
| degrade_chain | [decision_model, llm, experience] | 链顺序；experience 恒兜底 |
| provider_timeouts | collect 5 / decide 8 / analysis 3 | 各阶段时钟预算（秒），超时降级非失败 |

## output

| 键 | 默认 | 说明 |
|---|---|---|
| format | json_minimal | `json_minimal`（纯结构化）\| `json_full`（+理由/显示名）\| `text`（人读文本） |

## decision_model（判断模型层，可选）

endpoint + model **都配置才启用该层**；密钥经 `api_key_env` 指向的环境变量注入（仓库零厂商信息）。

| 键 | 说明 |
|---|---|
| endpoint | 服务地址（SystemOne 线协议：POST {endpoint}/v1/systemone） |
| api_key_env | 密钥所在环境变量名；null = 无需凭据（自托管） |
| model | 模型名（官方必填字段） |
| timeout_ms | 单次请求超时 |

## llm（判断②级兜底，可选，OpenAI 兼容）

base_url + model 都配置才启用。字段同上（base_url/api_key_env/model/timeout_ms）。

## http

| 键 | 说明 |
|---|---|
| auth.enabled | API Key/Bearer 鉴权开关（默认 false，生产开启——红线 15） |
| auth.api_key_env | 密钥环境变量名 |

## weights_override（可选，L1 热调权）

`weights_override.<scene>.<dimension>: 数值` 覆盖模板基线权重（请求级）。
owner 级个性化调制另有 evolved/users/{owner}/weights/ 生长通道（feedback/shadow，§5.5）。

## 密钥

一律环境变量（`.env` 已 gitignore；或 shell 导出）。`api_key_env` 只存**变量名**。

## 标准能力名（工具覆盖契约）

工具通过 `name` 字段声明提供什么能力。同名覆盖内置、异名共存。
场景模板 `info_needs.jsonc` 的 `capability` 字段引用这些标准名。

| 标准能力名 | 说明 | 产出 schema |
|---|---|---|
| `location` | 用户位置 | `{lat, lng, city, district}` |
| `weather` | 天气 | `{condition, temperature_c}` |
| `poi_search` | 候选搜索 | `[{id, name, tags, ...}]` |
| `traffic` | 交通状况 | `{level, ...}` |
| `deals` | 优惠信息 | `{...}` |

**覆盖规则**：
- 用户工具 `name` 与内置一致 → **覆盖**内置（仅本次请求或持久注册）
- `name` 不同 → **新能力**，与内置共存（info_needs 需显式引用才会使用）

**查询方式**：
```bash
decide-agent tools                    # CLI
curl http://localhost:8000/v1/tools  # HTTP
```
