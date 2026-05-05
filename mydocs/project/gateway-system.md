# Gateway System Truth

## 文件定位

`project/gateway-system.md` 记录 LuckBot 当前 gateway 接入层的真实结构与边界。

本文只覆盖：

- FastAPI gateway 自身
- Feishu adapter
- CLI remote over gateway
- dispatcher / service 与 runtime 的关系

## 模块职责

当前 gateway 模块负责：

- 暴露长期运行的 HTTP 服务
- 接收飞书 webhook
- 接收 CLI remote 请求
- 把外部输入规范化为统一 `IncomingEnvelope`
- 调度一次 LuckBot runtime 执行
- 对同一逻辑会话做基础并发保护

## 当前真实行为

### 1. 当前 gateway 不只接飞书，也承载 CLI remote

真实入口位于：

- `src/luckbot/adapters/gateway/app.py`

当前 HTTP 路由包括：

- `GET /healthz`
- `POST /webhooks/feishu/events`
- `POST /gateway/cli/turn`
- `POST /gateway/cli/command`

所以当前 gateway 不是“只有飞书接入”，而是：

- 飞书远程入口
- CLI 通过 `--gateway` 复用的本地 HTTP 入口

### 2. gateway 当前是 FastAPI 外层接入，不属于 plugin system

gateway 位于：

- `src/luckbot/adapters/gateway/`

它不通过 plugin system 注册平台能力。

当前分层是：

- `app.py`
  - FastAPI 入口与路由
- `dispatcher.py`
  - 平台无关的调度与并发保护
- `service.py`
  - 把统一消息转成一次 LuckBot runtime 调用
- `feishu/`
  - 飞书协议适配与 OpenAPI client
- `types.py`
  - `IncomingEnvelope`、`OutboundTarget` 等统一协议

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
- owner：`feishu:user:<open_id>`

CLI remote 当前映射为：

- session：`gateway:cli:<session_name>`
- owner：默认 `local`

所以当前事实是：

- gateway CLI 与本地 CLI transcript 不共享
- 但 gateway CLI 与本地 CLI 默认共享长期记忆 owner `local`

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

### 6. service 复用统一 runtime，不维护第二套 agent loop

桥接层位于：

- `src/luckbot/adapters/gateway/service.py`

当前会：

- 用 `PluginManager` 装配内置插件与项目级 `.luckbot/plugins`
- 先尝试执行 slash command
- 否则调用 `run_runtime()`

所以当前 gateway 没有独立 agent loop，只是 runtime 的另一种入口。

### 7. gateway 默认也使用项目级 `.luckbot` 配置与 state

当前 gateway service 默认从项目根解析：

- `.luckbot/plugins`

session / memory / gateway 自身运行态默认落在：

- `.luckbot/state/sessions`
- `.luckbot/state/memory`
- `.luckbot/state/gateway`

所以从 repo 外 cwd 启动 `luckbot` 或 gateway 时，项目级配置与运行态仍会落到同一个项目根。

### 8. 当前请求级身份透传已经打通到 runtime / session / memory

当前 gateway 会把这些字段透传到统一 runtime：

- `session_key`
- `owner_id`
- `platform`
- `chat_type`
- `chat_id`
- `user_id`
- `message_id`

因此当前 `SessionPlugin` 与 `MemoryPlugin` 会优先使用本次请求传入的身份，而不是只依赖环境变量默认值。

## 关键入口

- `src/luckbot/adapters/gateway/app.py`
- `src/luckbot/adapters/gateway/dispatcher.py`
- `src/luckbot/adapters/gateway/service.py`
- `src/luckbot/adapters/gateway/types.py`
- `src/luckbot/adapters/gateway/feishu/adapter.py`
- `src/luckbot/adapters/gateway/feishu/client.py`

## 边界

- gateway 负责平台接入与消息编排
- runtime 仍是统一执行内核
- 飞书特有协议只应停留在 `adapters/gateway/feishu/`
- gateway 当前只解决个人使用与轻量共享入口，不解决复杂多租户部署

## 风险

- 飞书当前只做基础请求体验证，严格签名校验尚未实现
- 并发保护是单进程内存态，多实例部署会失效
- responder 仍是状态卡片 / 最终卡片级别，不是细粒度 token streaming
