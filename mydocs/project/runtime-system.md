# Runtime System Truth

## 文件定位

`project/runtime-system.md` 记录 LuckBot 统一 runtime 模块当前已经实现的真实行为。

本文只写当前代码事实，不写未来设想。当前边界是：

- runtime 是标准 agent 运行时
- runtime 负责统一执行骨架，不负责业务插件内容本身
- runtime 层不再维护 `subagent` 一等概念

## 模块职责

LuckBot 当前的 runtime 模块负责：

- 定义一次 agent run 的输入、profile 与输出
- 串联 `before_run / after_run` 与 ReAct 内循环
- 支持复用外部 `PluginManager`
- 支持内部创建并销毁独立 `PluginManager`
- 统一所有 agent run 对 `run_react_loop()` 的使用方式
- 透传一次 run 的逻辑身份，如 `session_key` 与 `owner_id`
- 承载一次 run 的观测身份，如 `trace_id`、`run_id` 与 gateway 元数据

## 当前真实行为

### 1. 当前统一入口是 `run_runtime()`

runtime 当前核心定义分布在：

`src/luckbot/core/runtime/types.py`：
- `RuntimeContext`
- `RuntimeProfile`
- `RuntimeResult`
- `current_runtime_context()`
- `use_runtime_context(ctx)`

`src/luckbot/core/runtime/core.py`：
- `run_runtime()`

`src/luckbot/core/runtime/react_loop.py`：
- `run_react_loop()`

当前 `run_runtime()` 是唯一标准执行入口。

当前 `RuntimeContext` 是一次 agent run 的驱动数据结构。
它同时承载调用方输入、运行期身份与本轮可变运行态；项目内不再保留旧输入对象。

当前 `RuntimeContext` 中与身份相关的核心字段包括：

- `session_key`
- `owner_id`
- `trace_id`
- `run_id`
- `platform`
- `gateway_message_id`

其中：

- `session_key` 用于活动会话与 transcript 归属
- `owner_id` 用于长期记忆目录归属
- `trace_id` 用于一次请求链路关联
- `run_id` 用于 runtime 根执行标识与 LangSmith 根 run 关联

`before_run` 可以通过 `BeforeRunResult.session_key` / `BeforeRunResult.owner_id` 改写运行身份。
改写后的身份会继续影响后续 `before_llm_call`、tool 执行、`after_run` 与 memory owner 选择。

### 2. 当前 runtime 支持两种 manager 生命周期

`RuntimeContext` 当前允许两种 manager 生命周期：

- 传入外部 `plugin_manager`
- 不传 `plugin_manager`，由 runtime 自己创建

当前语义：

- CLI / gateway 这类外层装配入口可以复用外部 `PluginManager`
- 独立 runtime run 可以由 runtime 内部创建并销毁自己的 `PluginManager`

### 3. 当前 profile 负责表达运行时差异

`RuntimeProfile` 当前包含：

- `plugins`
- `enable_run_hooks`

其中 `plugins` 是 runtime 自建 `PluginManager` 时唯一的能力注入入口。
tool、hook、service 都应由插件在 `PluginContext` 中注册。

`enable_run_hooks` 决定是否启用 `before_run / after_run`。

### 4. 当前 runtime 只有一种输入消息形态

`RuntimeContext` 当前通过 `messages` 表达一次 agent run 的完整输入消息链。

当前规则是：

- `messages` 不能为空
- runtime 不再接收 `user_input`
- runtime 不再接收 `conversation_history`
- runtime 不负责追加本轮 `HumanMessage`

当前附带规则：

- 调用方负责在进入 runtime 前构造完整 messages
- CLI / gateway 会把旧历史消息与本轮用户输入组装为完整 messages
- `before_run` hook 当前看到的是完整 messages，并可整体替换
- 请求级身份透传由 `RuntimeContext` 字段决定，与输入消息来源无关
- runtime 不根据“主 agent / 子 agent”概念分叉，只根据 `RuntimeContext` 字段执行

### 5. 当前 CLI / gateway 通过统一 AgentTurnRunner 使用 runtime

当前 CLI 与 gateway 不再分别维护 command / runtime 编排。

当前事实是：

- CLI local 在 `src/luckbot/entrypoints/cli.py` 中构造 `IncomingTurn(channel="cli", transport="local")`
- CLI remote 经 `POST /gateway/cli/turn` 构造 `IncomingTurn(channel="cli", transport="http")`
- Feishu webhook 构造 `IncomingTurn(channel="feishu", transport="webhook")`
- 三者都进入 `src/luckbot/application/turn_runner.py`
- `AgentTurnRunner` 负责 command 分发、inline skill directive 解析、`RuntimeContext` 构造与 `run_runtime()` 调用

### 6. 当前 memory flush 直接使用统一 runtime

`src/luckbot/domains/memory/flush_agent.py` 当前负责：

- 构造 memory flush 专用 system prompt
- 构造 memory flush 输入 messages
- 通过 `RuntimeProfile.plugins` 注入 `MemoryFlushToolsPlugin`
- 调用 `run_runtime()`
- 结束后执行 `index.sync()`

memory flush 当前不是 runtime 层的特殊 agent 类型，只是一次由 `RuntimeContext` 驱动的独立 runtime run。

### 7. 当前 `run_react_loop()` 已下沉到 runtime 层

`run_react_loop()` 当前位于 `src/luckbot/core/runtime/react_loop.py`。

它当前负责：

- `before_llm_call`
- LLM 调用
- `after_llm_call`
- tool call 执行
- `before_tool_call / after_tool_call`

当前 `run_runtime()` / `run_react_loop()` 会继续把身份字段下发到 hook 输入：

- `BeforeRunInput.runtime_context`
- `BeforeRunInput.session_key`
- `BeforeRunInput.owner_id`
- `BeforeRunInput.trace_id`
- `BeforeRunInput.run_id`
- `BeforeLLMCallInput.runtime_context`
- `BeforeLLMCallInput.session_key`
- `BeforeLLMCallInput.owner_id`
- `BeforeLLMCallInput.trace_id`
- `BeforeLLMCallInput.run_id`
- `BeforeToolCallInput.runtime_context`
- `AfterToolCallInput.runtime_context`
- `AfterLLMCallInput.runtime_context`
- `AfterRunInput.runtime_context`
- `AfterRunInput.session_key`
- `AfterRunInput.owner_id`
- `AfterRunInput.trace_id`
- `AfterRunInput.run_id`

所有 agent run 都共用这套 LLM/tool 交替执行骨架。

### 8. 当前 runtime 已承担 CLI 与 gateway 的共同身份承载层

当前 runtime 不再只面向 CLI。

代码事实是：

- CLI 仍通过环境变量给出默认身份
- gateway 会在每次请求里显式传入 `session_key` / `owner_id`
- gateway 还会显式传入 `trace_id`、平台、消息 id 等观测身份
- runtime 不负责决定这些字段的映射规则，只负责透传给插件层

## 关键入口

- `src/luckbot/core/runtime/core.py`
  - `run_runtime`
- `src/luckbot/core/runtime/types.py`
  - `RuntimeContext`
  - `RuntimeProfile`
  - `RuntimeResult`
- `src/luckbot/core/runtime/react_loop.py`
  - `run_react_loop`
- `src/luckbot/core/runtime/__init__.py`
  - runtime 对外导出层

## 边界

- runtime 负责统一执行骨架与 manager 生命周期装配
- runtime 负责承载并透传 run 级身份
- runtime 会将 `session_key` / `owner_id` 显式合并进 metrics、log 与 LangSmith metadata
- runtime 不负责具体 memory / session / skills / observability 展示业务逻辑
- runtime 不负责决定 gateway 平台身份如何映射成 `session_key` / `owner_id`
- `PluginManager` 仍是底层装配器，不理解主 agent / 子 agent 概念
- 所有运行差异当前属于 `RuntimeContext` / `RuntimeProfile`，不属于执行入口差异

## 风险

- 外部复用 manager 与内部拥有 manager 的两种路径都属于核心语义，改动时需要同时验证
- hook 能直接看到 `RuntimeContext`，改动字段语义时必须同步 hook contract 与测试
- `session_key` / `owner_id` / `trace_id` 现在都已成为 runtime 核心字段；其中 `session_key` / `owner_id` 不再双写到 observability context
