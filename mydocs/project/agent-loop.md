# Agent Loop Truth

## 文件定位

`project/agent-loop.md` 记录 LuckBot 当前 agent run 生命周期与 ReAct loop 的真实行为。

本文当前边界是：

- runtime 层不再存在 `agent_loop()` 兼容入口
- 完整 run 生命周期由 `run_runtime()` 承载
- LLM / tool 交替执行骨架由 `run_react_loop()` 承载
- CLI / gateway 负责把用户输入组装成 `RuntimeContext.messages`

## 模块职责

LuckBot 当前的 agent loop 相关模块负责：

- `run_runtime()`：执行一次完整 agent run 生命周期
- `run_react_loop()`：执行 ReAct 内循环
- 插件 hook：在固定生命周期节点改写工具、prompt、messages 或执行收尾逻辑

## 当前真实行为

### 1. 当前 runtime 入口是 `run_runtime()`

当前完整 agent run 入口位于：

- `src/luckbot/core/runtime/core.py`
  - `run_runtime()`

当前 ReAct 内循环位于：

- `src/luckbot/core/runtime/react_loop.py`
  - `run_react_loop()`

`src/luckbot/core/runtime/agent_loop.py` 当前已删除。

### 2. 当前调用方负责构造完整 messages

runtime 当前不接收 `user_input`，也不负责追加当前用户消息。

当前事实是：

- CLI 在 `src/luckbot/entrypoints/cli.py` 中解析用户输入并构造 `RuntimeContext`
- gateway 在 `src/luckbot/adapters/gateway/service.py` 中解析消息并构造 `RuntimeContext`
- memory flush 在 `src/luckbot/domains/memory/flush_agent.py` 中直接构造完整 messages

CLI / gateway 当前都会：

- 调用 `parse_skill_activation_directive(...)`
- 把历史消息与 `HumanMessage(normalized_user_input)` 组装为 `RuntimeContext.messages`
- 把 `requested_skill` 传入 `RuntimeContext`
- 调用 `run_runtime()`

### 3. 当前 `before_run` 决定本轮初始工具、prompt 与消息链

`run_runtime()` 当前会先：

- 校验 `RuntimeContext`
- 装配或复用 `PluginManager`
- 取 `plugin_manager.get_tools()` 作为初始工具表
- 取调用方传入的 `system_prompt`
- 复制一份 `RuntimeContext.messages`

然后按 hook 排序依次执行所有 `before_run` hook。

每个 hook 当前都可以返回 `BeforeRunResult`，改写：

- `tools`
- `system_prompt`
- `messages`

当前语义是串联改写：

- 前一个 hook 的输出
- 会成为后一个 hook 的输入

当前内置链路里已经显式约束了这些关键顺序：

- `memory -> session`
- `skills -> skill activation`

因此当前 `before_run` 是：

- session 恢复旧 transcript 的入口
- MCP 合并本轮工具的入口
- skill activation 预加载写入状态的入口

### 4. 当前 `run_react_loop()` 每轮都会重新取 LLM 并重建本轮快照

`run_react_loop()` 一开始当前会调用：

- `build_llm()`

`build_llm()` 位于 `src/luckbot/core/llm/client.py`，当前要求这些环境变量都非空：

- `LUCKBOT_MODEL_BASE_URL`
- `LUCKBOT_MODEL_API_KEY`
- `LUCKBOT_MODEL_NAME`

缺失时会直接抛 `ValueError`。

当前 LLM 客户端固定为：

- `ChatOpenAI`
- `temperature=0.2`

在 ReAct 的每一轮迭代里，`run_react_loop()` 都会：

- 从 `RuntimeContext.current_tools` 复制出 `current_tools`
- 从 `RuntimeContext.current_prompt` 复制出 `current_prompt`

再交给当轮 `before_llm_call` 继续改写。

这意味着：

- `before_llm_call` 的工具和 prompt 改写会写回 `RuntimeContext.current_tools/current_prompt`
- 下一轮 ReAct 迭代会从 `RuntimeContext` 的当前运行态继续启动

### 5. 当前 `before_llm_call` 可以改写工具、prompt 和消息链

每轮迭代前，`run_react_loop()` 当前会按 hook 排序执行所有 `before_llm_call` hook。

每个 hook 可以返回 `BeforeLLMCallResult` 改写：

- `tools`
- `system_prompt`
- `messages`

当前 `messages` 的改写方式是整体替换：

- 若某个 hook 返回 `messages`
- loop 会先 `messages.clear()`
- 再 `messages.extend(result.messages)`

当前这正是这些能力生效的位置：

- skills 把 L0/L1/L2 注入 system prompt
- memory 在预算超限时触发压缩与 memory flush
- session 在 memory 等插件之后观察消息链是否被压缩变短
- observability 记录当轮 prompt、消息链、tool 与 LLM 过程

### 6. 当前 memory 压缩链会在 runtime 内直接改写消息链

`src/luckbot/domains/memory/compaction.py` 中的 `maybe_compact_before_llm()` 由 `MemoryPlugin` 注册为 `before_llm_call`。

它当前只影响：

- 当轮传给模型的 `messages`

不直接负责：

- transcript 落盘

当前压缩触发后，可能会：

1. 运行 memory flush 独立 runtime run
2. 保留最近 `K` 条消息
3. 对更早历史调用 LLM 生成摘要
4. 用一个带内部标记的 compaction 摘要 `SystemMessage` 加最近消息组成新链

随后 `SessionPlugin._before_llm()` 会检测：

- 当前消息链是否比已持久化链更短

若更短，则标记 `after_run` 时做全量 transcript 重写。

该摘要消息当前会被 transcript 白名单持久化，并在后续恢复时重建为等价摘要消息。

### 7. 当前模型调用使用 `SystemMessage + messages` 的组合

完成 `before_llm_call` 后，`run_react_loop()` 当前会：

- 构造 `SystemMessage(content=current_prompt)`
- 再拼接当前 `messages`
- 调用 `llm.ainvoke(...)`

因此：

- `system_prompt` 不会被追加进持久化消息链
- 它只作为每次模型调用的首条系统消息参与推理

### 8. 当前工具调用通过 LangChain tool call 驱动

当模型返回带 `tool_calls` 的 `AIMessage` 时：

- 该 `AIMessage` 会先追加到 `messages`
- runtime 会并发执行所有 tool call
- 每个工具调用会产出一个 `ToolMessage`
- 所有 `ToolMessage` 会追加到 `messages`
- loop 进入下一轮 LLM 调用

单个工具调用内部顺序是：

1. 执行 `before_tool_call`
2. 调用工具
3. 执行 `after_tool_call`
4. 生成 `ToolMessage`

### 9. 当前结束条件

`run_react_loop()` 当前结束条件是：

- 模型返回不含 tool calls 的 `AIMessage`
- 或达到 `max_steps`

当模型返回不含 tool calls 的 `AIMessage` 时：

- 该消息会追加到 `messages`
- assistant 最后一条无工具调用消息的 content，就是 run 返回的最终文本

如果达到 `max_steps`：

- 返回固定文本：`已达最大步数，任务可能未完成。`

### 10. 当前异常与 after_run 语义

如果异常发生在：

- `before_run`
- `build_llm()`
- 某次 `before_llm_call`
- LLM 调用
- `after_llm_call`

`run_runtime()` 当前会：

- 记录异常
- 继续尝试执行 `after_run`
- 然后重新抛出原异常

`after_run` 当前能收到：

- 已生成的 `result`
- 当前 `messages`
- `session_key`
- `owner_id`
- `trace_id`
- `run_id`
- `error`

若 `after_run` 自身抛错：

- 若前面已有主异常，优先抛主异常
- 若前面没有主异常，则抛 `after_run` 异常

### 11. 当前 runtime 与 observability 上下文职责分离

`run_runtime()` 当前会由 `RuntimeContext.to_observability_context()` 建立一次 run 的观测上下文。
runtime context 与 observability context 是两个独立 contextvar；前者服务运行期身份与工具，后者服务指标、span 与 LangSmith。

当前 `ObservabilityContext` 承载：

- `trace_id`
- `run_id`
- `platform`
- `gateway_message_id`
- `gateway_trace_id`
- `chat_id`
- `user_id`

`session_key` / `owner_id` 只存放在 `RuntimeContext` 上。
runtime 需要观测输出这些字段时，会从 `RuntimeContext` 显式合并到 attrs / metadata / tags。

runtime 身份字段会继续下发到：

- `BeforeRunInput`
- `BeforeLLMCallInput`
- `AfterRunInput`
- tool hook 输入

所有 hook input 当前也都携带 `runtime_context`。

## 关键入口

- `src/luckbot/core/runtime/core.py`
  - `run_runtime`
- `src/luckbot/core/runtime/types.py`
- `RuntimeContext`
- `RuntimeProfile`
- `RuntimeResult`
- `current_runtime_context`
- `use_runtime_context`
  - `DEFAULT_MAX_STEPS`
- `src/luckbot/core/runtime/react_loop.py`
  - `run_react_loop`
- `src/luckbot/core/llm/client.py`
  - `build_llm`

## 边界

- runtime 负责执行一次由 `RuntimeContext` 描述的 agent run
- runtime 不负责决定 CLI / gateway 如何把用户输入映射成 messages
- runtime 不负责 memory / session / skills / mcp 的业务语义
- `run_react_loop()` 只负责 ReAct 内循环，不负责 manager 生命周期
