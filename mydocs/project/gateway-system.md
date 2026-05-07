# Gateway System Truth

## 文件定位

`project/gateway-system.md` 记录 LuckBot 当前 gateway 接入层的真实结构与边界。

本文只覆盖：

- FastAPI gateway 自身
- Feishu adapter
- CLI remote over gateway
- dispatcher / application turn runner 与 runtime 的关系

## 模块职责

当前 gateway 模块负责：

- 暴露长期运行的 HTTP 服务
- 接收飞书 webhook
- 接收 CLI remote 请求
- 把外部输入规范化为统一 `IncomingTurn`
- 通过统一 `AgentTurnRunner` 调度一次 LuckBot 执行
- 对同一逻辑会话做基础并发保护

## 当前真实行为

### 1. 当前 gateway 不只接飞书，也承载 CLI remote

真实入口位于：

- `src/luckbot/adapters/gateway/app.py`

当前 HTTP 路由包括：

- `GET /healthz`
- `POST /webhooks/feishu/events`
- `POST /gateway/cli/turn`

其中 `/gateway/cli/turn` 可以通过 `LUCKBOT_GATEWAY_SERVER_KEY` 开启简单 Bearer key 校验。
`/healthz` 不校验，飞书 webhook 继续使用飞书自己的 token / app_id 校验。

所以当前 gateway 不是“只有飞书接入”，而是：

- 飞书远程入口
- CLI 通过 `luckbot gateway` 复用的本地 HTTP 入口

### 2. gateway 当前是 FastAPI 外层接入，不属于 plugin system

gateway 位于：

- `src/luckbot/adapters/gateway/`

它不通过 plugin system 注册平台能力。

当前分层是：

- `app.py`
  - FastAPI 入口与路由
- `dispatcher.py`
  - 平台无关的调度与并发保护
- `feishu/`
  - 飞书协议适配与 OpenAPI client
- `types.py`
  - gateway adapter / runner 协议；turn 数据结构来自 `luckbot.application.turns`

### 3. Feishu 当前只支持文本消息主流程

飞书 adapter 位于：

- `src/luckbot/adapters/gateway/feishu/adapter.py`

当前负责：

- URL verification challenge
- 基于 token / app_id 的基础请求校验
- 文本消息提取
- 群聊 `@` 过滤
- responder 创建

当前不支持：

- 图片 / 文件 / 语音进入 agent 主流程
- 复杂交互卡片回调
- 严格签名算法校验

### 4. 飞书与 CLI remote 的身份规则不同

飞书当前映射为：

- 私聊 session：`feishu:<open_id>`
- 群聊 session：`feishu:group:<chat_id>:<open_id>`
- owner：`LUCKBOT_OWNER_ID`；未设置时 fallback `feishu:user:<open_id>`

CLI remote 当前映射为：

- session：`gateway:cli:<session_name>`
- owner：`LUCKBOT_OWNER_ID`；未设置时使用请求 owner，仍为空则 fallback `local`

所以当前事实是：

- gateway CLI 与本地 CLI transcript 不共享
- 不同 channel 的 session 不共享
- 单用户部署下，CLI 与飞书可通过相同 `LUCKBOT_OWNER_ID` 共享长期记忆

### 5. dispatcher 对 Feishu 和 CLI remote 的执行模式不同

`src/luckbot/adapters/gateway/dispatcher.py` 当前提供两种模式：

- `enqueue(...)`
  - 先 ACK，再后台异步执行
  - 主要给飞书 webhook 使用
- `run_inline(...)`
  - 同步等待本次 run 返回
  - 主要给 `/gateway/cli/*` 使用

共同点是：

- 同一 `session_key` 已在执行时会拒绝新请求
- 当前并发保护是进程内字典级别，不是分布式锁

### 6. gateway 复用统一 AgentTurnRunner，不维护第二套 agent loop

统一执行层位于：

- `src/luckbot/application/turn_runner.py`
- `src/luckbot/application/turns.py`

当前会：

- 接收 `IncomingTurn`
- 用 `PluginManager` 装配内置插件与项目级 `.luckbot/plugins`
- 先尝试执行 slash command
- 否则调用 `run_runtime()`

所以当前 gateway 没有独立 service / agent loop，只是把平台输入转成 `IncomingTurn` 后交给 application 层。

### 7. CLI 侧 gateway 应用层负责连接 gateway bot

CLI 侧 gateway 应用层位于：

- `src/luckbot/application/gateway/client.py`
- `src/luckbot/application/gateway/control.py`

其中：

- `client.py`
  - 负责本地 CLI 到 gateway bot 的 HTTP 调用
  - 当前使用 `/gateway/cli/turn` 与 `/healthz`
  - `luckbot gateway` 不带 URL 时，默认启动或复用本机 gateway
  - `luckbot gateway <url>` 带 URL 时，只连接外部 gateway bot，不启动本机 gateway
  - 若设置 `LUCKBOT_GATEWAY_CLIENT_KEY`，请求会携带 `Authorization: Bearer <key>`
- `control.py`
  - 只负责本地开发 / 本地常驻 gateway 的进程管理
  - 维护 pid、state 与 log 文件

所以这层不是 gateway 服务端，也不是 plugin service。
它是 CLI 侧的 application gateway adapter，用于把本地 CLI 操作转换为 gateway bot 调用。

当前 gateway CLI 相关环境变量：

- `LUCKBOT_GATEWAY_URL`
  - client 连接的 gateway base URL；`luckbot gateway <url>` 会在本次进程内覆盖它
- `LUCKBOT_GATEWAY_CLIENT_KEY`
  - client 连接密钥，会作为 Bearer token 发到 `/gateway/cli/turn`
- `LUCKBOT_GATEWAY_SERVER_KEY`
  - gateway server 被连接密钥；为空时不启用 CLI remote 鉴权
- `LUCKBOT_GATEWAY_HOST` / `LUCKBOT_GATEWAY_PORT`
  - 本地 gateway 监听地址与端口

### 8. gateway 默认也使用项目级 `.luckbot` 配置与 state

当前 `AgentTurnRunner` 默认从项目根解析：

- `.luckbot/plugins`

session / memory / gateway 自身运行态默认落在：

- `.luckbot/state/sessions`
- `.luckbot/state/memory`
- `.luckbot/state/gateway`

所以从 repo 外 cwd 启动 `luckbot` 或后台 gateway 时，项目级配置与运行态仍会落到同一个项目根。

### 9. 当前请求级身份透传已经打通到 runtime / session / memory

当前 gateway 会把这些字段透传到统一 runtime：

- `session_key`
- `owner_id`
- `channel`，进入 runtime 后映射为观测 `platform`
- `chat_id`
- `user_id`
- `message_id`，进入 runtime 后映射为 `gateway_message_id`

`transport` 当前用于 `CommandContext`，不进入 `RuntimeContext`。

因此当前 `SessionPlugin` 与 `MemoryPlugin` 会优先使用本次请求传入的身份，而不是只依赖环境变量默认值。
gateway dispatcher 的 observability attrs 也会显式合并 `session_key` / `owner_id`，但这些字段不存入 `ObservabilityContext`。

## 关键入口

- `src/luckbot/adapters/gateway/app.py`
- `src/luckbot/adapters/gateway/dispatcher.py`
- `src/luckbot/adapters/gateway/types.py`
- `src/luckbot/adapters/gateway/feishu/adapter.py`
- `src/luckbot/adapters/gateway/feishu/client.py`
- `src/luckbot/application/turns.py`
- `src/luckbot/application/turn_runner.py`

## 边界

- gateway 负责平台接入与消息编排
- runtime 仍是统一执行内核
- 飞书特有协议只应停留在 `adapters/gateway/feishu/`
- gateway 当前只解决个人使用与轻量共享入口，不解决复杂多租户部署

## 风险

- 飞书当前只做基础请求体验证，严格签名校验尚未实现
- 并发保护是单进程内存态，多实例部署会失效
- responder 仍是状态卡片 / 最终卡片级别，不是细粒度 token streaming
