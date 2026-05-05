# Runtime System Truth

## 文件定位

`project/runtime-system.md` 记录 LuckBot 统一 runtime 模块当前已经实现的真实行为。

本文只写当前代码事实，不写未来设想。当前边界是：

- runtime 是主 agent 与 subagent 共用的运行时内核
- runtime 负责统一执行骨架，不负责业务插件内容本身

## 模块职责

LuckBot 当前的 runtime 模块负责：

- 定义统一的 runtime 输入、profile 与输出
- 串联 `before_run / after_run` 与 ReAct 内循环
- 支持复用外部 `PluginManager`
- 支持内部创建并销毁独立 `PluginManager`
- 统一主 agent 与 subagent 对 `run_react_loop()` 的使用方式
- 透传一次 run 的逻辑身份，如 `session_key` 与 `owner_id`
- 承载一次 run 的观测身份，如 `trace_id`、`run_id` 与 gateway 元数据

## 当前真实行为

### 1. 当前统一入口是 `run_runtime()`

`src/luckbot/core/runtime/core.py` 当前定义：

- `RuntimeSpec`
- `RuntimeProfile`
- `RuntimeResult`
- `run_runtime()`
- `run_react_loop()`

当前 `run_runtime()` 是统一执行入口。

当前 `RuntimeSpec` 中与身份相关的核心字段包括：

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

### 2. 当前 runtime 支持两种 manager 生命周期

`RuntimeSpec` 当前允许两种运行模式：

- 传入外部 `plugin_manager`
- 不传 `plugin_manager`，由 runtime 自己创建

当前语义：

- 主 agent 当前复用外部 `PluginManager`
- subagent 当前由 runtime 内部创建并销毁自己的 `PluginManager`

### 3. 当前 profile 负责表达运行时差异

`RuntimeProfile` 当前包含：

- `plugins`
- `tool_providers`
- `service_providers`
- `enable_run_hooks`
- `normalize_remote_skill_directive`

当前主 agent 与 subagent 的差异主要通过 profile 表达，而不是通过两套独立执行内核表达。

### 4. 当前 runtime 有两种输入模式

`RuntimeSpec` 当前支持：

- `user_input + conversation_history`
- `messages`

当前规则是：

- `messages` 模式用于受限运行时
- `messages` 模式当前不允许 `before_run / after_run`
- `user_input` 模式用于主 agent 这类完整 run 生命周期

当前附带规则：

- `messages` 模式虽然仍可传 `session_key` / `owner_id`，但由于不走 `before_run / after_run`，相关内置插件生命周期不会被触发
- 请求级身份透传的主要生效路径是完整 `user_input` run

### 5. 当前主 agent 是 runtime 的薄适配层

`src/luckbot/core/runtime/agent_loop.py` 当前不再持有完整编排逻辑。

它当前负责：

- 保持原有主 agent 对外参数形状
- 构造主 agent profile
- 调用 `run_runtime()`

### 6. 当前 subagent 是 runtime 的薄适配层

`src/luckbot/core/subagent/runtime.py` 当前负责：

- 保持 `SubagentRunRequest / SubagentRunResult` 这层受限调用面
- 把 plugins / providers / messages 组装进受限 profile
- 调用 `run_runtime()`

当前 subagent 不再自己创建独立执行内核，只是统一 runtime 的一个 profile 适配层。

### 7. 当前 `run_react_loop()` 已下沉到 runtime 层

`run_react_loop()` 当前位于 `src/luckbot/core/runtime/core.py`。

它当前负责：

- `before_llm_call`
- LLM 调用
- `after_llm_call`
- tool call 执行
- `before_tool_call / after_tool_call`

当前 `run_runtime()` / `run_react_loop()` 会继续把身份字段下发到 hook 输入：

- `BeforeRunInput.session_key`
- `BeforeRunInput.owner_id`
- `BeforeRunInput.trace_id`
- `BeforeRunInput.run_id`
- `BeforeLLMCallInput.session_key`
- `BeforeLLMCallInput.owner_id`
- `BeforeLLMCallInput.trace_id`
- `BeforeLLMCallInput.run_id`
- `AfterRunInput.session_key`
- `AfterRunInput.owner_id`
- `AfterRunInput.trace_id`
- `AfterRunInput.run_id`

它当前同时被：

- 主 agent runtime
- subagent runtime

共用。

### 8. 当前 runtime provider 通过内部 plugin 注入

`RuntimeToolProvider` 与 `RuntimeServiceProvider` 当前是轻量 provider 协议。

当 runtime 采用“自建 manager”模式且收到 providers 时，当前会通过内部 `_RuntimeProvidersPlugin` 把这些 provider 注册到统一 `PluginContext`。

### 9. 当前 runtime 已承担 CLI 与 gateway 的共同身份承载层

当前 runtime 不再只面向 CLI。

代码事实是：

- CLI 仍通过环境变量给出默认身份
- gateway 会在每次请求里显式传入 `session_key` / `owner_id`
- gateway 还会显式传入 `trace_id`、平台、消息 id 等观测身份
- runtime 不负责决定这些字段的映射规则，只负责透传给插件层

## 关键入口

- `src/luckbot/core/runtime/core.py`
  - `RuntimeSpec`
  - `RuntimeProfile`
  - `RuntimeResult`
  - `run_runtime`
  - `run_react_loop`
- `src/luckbot/core/runtime/__init__.py`
  - runtime 对外导出层
- `src/luckbot/core/runtime/agent_loop.py`
  - 主 agent 适配层
- `src/luckbot/core/subagent/runtime.py`
  - subagent 适配层

## 边界

- runtime 负责统一执行骨架与 manager 生命周期装配
- runtime 负责承载并透传 run 级身份
- runtime 不负责具体 memory / session / skills / observability 展示业务逻辑
- runtime 不负责决定 gateway 平台身份如何映射成 `session_key` / `owner_id`
- `PluginManager` 仍是底层装配器，不理解主 agent / subagent 概念
- 主 agent 与 subagent 的语义差异当前属于 runtime spec / profile，而不属于执行内核差异

## 风险

- runtime 当前同时承载主 agent 与 subagent 语义，profile 边界若继续膨胀，容易重新长成隐式分叉
- 外部复用 manager 与内部拥有 manager 的两种路径都属于核心语义，改动时需要同时验证
- `messages` 模式当前明确不走 `before_run / after_run`，若后续扩展这条规则，需要同步校正文档与测试
- `session_key` / `owner_id` / `trace_id` 现在都已成为 runtime 核心输入，后续若继续扩展 request context，容易把 runtime 变成隐式 transport 上下文容器
