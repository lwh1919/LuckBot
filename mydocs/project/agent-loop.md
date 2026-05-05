# Agent Loop Truth

## 文件定位

`project/agent-loop.md` 记录 LuckBot agent loop 当前已经实现的真实行为。

本文当前边界是：

- `src/luckbot/core/runtime/agent_loop.py` 是主 agent 对统一 runtime 的薄适配层
- 真正的执行骨架由 runtime 提供，loop 模块只保留主入口兼容面
- 消息持久化、记忆压缩、skill 注入、MCP 注入等行为最终都经由 runtime 调用插件 hook 发生

## 模块职责

LuckBot 当前的 agent loop 模块负责：

- 保持主 agent 既有调用参数与返回值形状
- 把主 agent 语义翻译为 `RuntimeSpec + RuntimeProfile`
- 复用外部 `PluginManager` 并委托 `run_runtime()`
- 对外继续导出 `run_react_loop()` 供兼容调用

## 当前真实行为

### 1. 当前主入口文件是 `src/luckbot/core/runtime/agent_loop.py`

当前对外公开的两个核心入口是：

- `agent_loop()`
- `run_react_loop()`

其中：

- `agent_loop()`
  - 是主 agent 薄适配层
  - 负责构造主 agent profile 后委托 `run_runtime()`
- `run_react_loop()`
  - 真实实现位于 runtime 模块
  - 这里仅继续对外导出该入口

`DEFAULT_MAX_STEPS` 当前固定为：

- `25`

CLI 会在 `src/luckbot/entrypoints/cli.py` 中调用 `agent_loop()`，并允许 `LUCKBOT_RECURSION_LIMIT` 覆盖单轮最大步数。

### 2. 当前 `agent_loop()` 的输入输出语义

`agent_loop()` 当前接收：

- `user_input`
- `plugin_manager`
- `system_prompt`
- `conversation_history`
- `session_key`
- `max_steps`
- `remote_skill`

当前返回：

- `(最终回答文本, messages)`

返回的 `messages` 当前语义是：

- 包含本轮完整 ReAct 链
- 包含 user / assistant / tool 消息
- 不包含 `SystemMessage`
- 可以直接作为下一轮 `conversation_history`

这也是当前 session 持久化与多轮续聊使用的消息形态。

### 3. 当前 `agent_loop()` 通过 profile 开启 remote skill 规范化

`agent_loop()` 本身当前不再直接解析 remote skill 文本协议。

它当前只会在构造 `RuntimeProfile` 时设置：

- `enable_run_hooks=True`
- `normalize_remote_skill_directive=True`

真正的 remote skill 规范化发生在 `run_runtime()` 中：

- 先调用 `extract_remote_skill_directive(user_input)`
- 把 `[use skill](name) ...` 或 `[use skill name] ...` 解析为内联请求
- 再通过 `_merge_remote_skills()` 合并显式 `remote_skill` 与内联请求
- 若两者 skill 名冲突，直接抛异常

所以当前 remote skill 规范化已经属于 runtime 行为，只是由主 agent profile 打开。

### 4. 当前 `before_run` 在 runtime 中决定本轮初始工具、prompt 与历史消息

当 `agent_loop()` 委托 `run_runtime()` 后，runtime 当前会先：

- 取 `plugin_manager.get_tools()` 作为初始工具表
- 取调用方传入的 `system_prompt`
- 复制一份 `conversation_history`

然后按插件初始化顺序依次执行所有 `before_run` hook。

当前顺序来源是：

- `PluginManager` 先按插件 `dependencies` 做拓扑排序
- 每个插件在 `initialize()` 中注册出来的 hook 再按该顺序进入运行时链路

每个 hook 当前都可以返回 `BeforeRunResult`，改写：

- `tools`
- `system_prompt`
- `conversation_history`

当前语义是串联改写：

- 前一个 hook 的输出
- 会成为后一个 hook 的输入

当前内置链路里已经显式约束了这些关键顺序：

- `memory -> session`
- `skills -> remote skill`

因此当前 `before_run` 是：

- session 恢复旧 transcript 的入口
- MCP 合并本轮工具的入口
- remote skill 预加载写入状态的入口

### 5. 当前 runtime 会在进入 ReAct 前把用户消息追加进消息链

`before_run` 结束后，runtime 当前会：

- 以处理后的 `conv_history` 复制出 `messages`
- 追加一个 `HumanMessage(content=normalized_user_input)`

然后把这条消息链交给 `run_react_loop()`。

所以当前事实是：

- `before_run` 看到的是“本轮 user 输入之前”的历史
- `before_llm_call` 看到的则已经包含当前 user 消息

### 6. 当前 `run_react_loop()` 每轮都会重新取 LLM 并重建本轮快照

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

- 从传入的 `tools` 复制出 `current_tools`
- 从传入的 `system_prompt` 复制出 `current_prompt`

再交给当轮 `before_llm_call` 继续改写。

这意味着：

- `before_llm_call` 的工具和 prompt 改写只影响当前迭代
- 不会直接反写到 `run_react_loop()` 的原始 `tools` / `system_prompt` 变量

### 7. 当前 `before_llm_call` 可以改写工具、prompt 和消息链

每轮迭代前，`run_react_loop()` 当前会按插件初始化顺序执行所有 `before_llm_call` hook。

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
- 统一 observability 会同步记录当轮 prompt、消息链、tool 与 LLM 过程

### 8. 当前 memory 压缩链会在 runtime 内直接改写消息链

`src/luckbot/domains/memory/compaction.py` 中的 `maybe_compact_before_llm()` 由 `MemoryPlugin` 注册为 `before_llm_call`。

它当前只影响：

- 当轮传给模型的 `messages`

不直接负责：

- transcript 落盘

当前压缩触发后，可能会：

1. 运行 memory flush 子 agent
2. 保留最近 `K` 条消息
3. 对更早历史调用 LLM 生成摘要
4. 用一个带内部标记的 compaction 摘要 `SystemMessage` 加最近消息组成新链

随后 `SessionPlugin._before_llm()` 会检测：

- 当前消息链是否比已持久化链更短

若更短，则标记 `after_run` 时做全量 transcript 重写。

该摘要消息当前会被 transcript 白名单持久化，并在后续恢复时重建为等价摘要消息。

所以当前 runtime 对消息链的处理，直接影响 session、memory 与 observability 三个模块；`agent_loop()` 只是主 agent 适配入口。

### 9. 当前模型调用使用 `SystemMessage + messages` 的组合

完成 `before_llm_call` 后，`run_react_loop()` 当前会：

- 若工具表非空，则执行 `llm.bind_tools(tool_list)`
- 否则直接使用原始 `llm`

真正发给模型的消息当前是：

- `[SystemMessage(content=current_prompt), *messages]`

也就是：

- system prompt 不在 `messages` 里常驻
- 而是在每轮调用模型时临时放到最前面

模型返回值当前要求是 `AIMessage`。

loop 会把这个 `AIMessage` 直接追加回 `messages`。

### 10. 当前无 tool call 时，assistant 内容就是最终返回文本

在每轮模型返回后，`run_react_loop()` 会先执行所有 `after_llm_call` hook。

然后检查：

- `response.tool_calls`

若为空，当前就结束整轮 ReAct。

最终文本的提取规则是：

- `response.content` 是字符串时直接用它
- 否则转成 `str(response.content or "")`

所以当前 loop 没有额外的“最终答案后处理器”。

assistant 最后一条无工具调用消息的 content，就是 run 返回的最终文本来源。

### 11. 当前有 tool call 时会并发执行工具

若模型返回了 tool calls，`run_react_loop()` 当前会：

- 为每个 tool call 创建一个 `_run_one_tool()` 任务
- 用 `asyncio.gather(..., return_exceptions=True)` 并发等待

之后按原始 tool call 顺序把结果追加回 `messages`。

当前即使工具是并发跑的，消息链里的 `ToolMessage` 顺序仍按：

- 模型给出的 tool call 顺序

而不是按真实完成先后顺序。

### 12. 当前 `_run_one_tool()` 负责工具级 hook 与异常归一化

`_run_one_tool()` 当前处理单个 tool call 的顺序是：

1. 读取 `tc["name"]`、`tc["args"]`、`tc["id"]`
2. 依次执行所有 `before_tool_call`
3. 用改写后的参数查找并执行工具
4. 依次执行所有 `after_tool_call`
5. 返回 `ToolMessage`

当前几种异常分支会被归一化成文本，而不是直接让整个 loop 失败：

- 工具名不存在
  - 输出 `错误：未知工具「...」`
- 工具执行抛异常
  - 输出 `执行工具「...」时出错: ...`
- `asyncio.gather` 得到异常对象
  - 输出 `工具异常: ...`

所以当前 tool error 大多会回到消息链里，作为模型下一轮可见的工具输出，而不是直接中止整个 run。

### 13. 当前达到最大步数时会返回固定兜底文本

`run_react_loop()` 当前用：

- `steps < max_steps`

控制循环。

只有在发生过至少一轮带工具调用的迭代后，`steps` 才会递增。

如果循环耗尽仍未得到“无 tool call 的 assistant 响应”，当前返回：

- `"已达最大步数，任务可能未完成。"`

并同时返回当前累计的 `messages`。

### 14. 当前 `after_run` 走 finally 路径，正常退出和异常退出都会执行

`agent_loop()` 当前会在 run 退出时统一执行 `after_run` hook。

当前这些行为都挂在这个收尾阶段：

- session transcript flush
- memory sync
- observability 收尾上报

如果异常发生在：

- remote skill 预处理
- `before_run`
- `build_llm()`
- 某次 `before_llm_call`
- 模型调用本身

当前 `after_run` 仍会执行。

但异常路径下传给 `after_run` 的输入语义是：

- `result` 可能为空字符串
- `messages` 可能是空链或部分消息链
- `error` 会携带本轮异常对象

所以当前依赖 `after_run` 的插件不能假设：

- 本轮一定产生了最终 answer
- `messages` 一定是完整 ReAct 链

### 15. 当前 `agent_loop()` 已退化为主 agent 薄适配层

`src/luckbot/core/runtime/agent_loop.py` 当前不再持有完整运行时编排逻辑。

它当前负责：

- 保持主 agent 原有对外参数形状
- 构造主 agent 的 runtime spec / profile
- 复用外部 `PluginManager`
- 调用统一 `run_runtime()`

当前统一 runtime 位于：

- `src/luckbot/core/runtime/core.py`

当前 loop 层的真实分工是：

- `agent_loop()` 负责主 agent 适配
- `run_react_loop()` 负责主 agent 与 subagent 共用的 LLM/tool 交替执行骨架
- `run_runtime()` 负责统一生命周期编排

### 16. 当前 loop 是业务模块的共同影响面

当前这些模块的运行时行为都直接依赖 loop：

- session
  - `before_run` 恢复历史
  - `before_llm_call` 检测压缩
  - `after_run` 落盘 transcript
- memory
  - `before_run` 注入 Memory Recall 约束
  - `before_llm_call` 压缩上下文
  - `after_run` 同步 memory 索引
- skills
  - `before_run` 重置状态
  - `before_llm_call` 注入 skill 内容
- remote skill
  - `before_run` 预加载 skill 状态
- mcp
  - `before_run` 合并 MCP 工具
- observability
  - 贯穿 gateway、runtime、tool、LLM 与 memory/session 的观测链路

所以 loop 改动通常不是局部改动，而是跨模块改动。

## 关键入口

- `src/luckbot/core/runtime/agent_loop.py`
  - `DEFAULT_MAX_STEPS`
  - `agent_loop`
- `src/luckbot/core/runtime/core.py`
  - `RuntimeSpec`
  - `RuntimeProfile`
  - `RuntimeResult`
  - `run_runtime`
  - `run_react_loop`
- `src/luckbot/core/llm/client.py`
  - `build_llm`
- `src/luckbot/core/plugin/hooks.py`
  - `BeforeRunInput`
  - `BeforeRunResult`
  - `BeforeLLMCallInput`
  - `BeforeLLMCallResult`
  - `AfterLLMCallInput`
  - `BeforeToolCallInput`
  - `AfterToolCallInput`
  - `AfterRunInput`
- `src/luckbot/domains/memory/compaction.py`
  - `maybe_compact_before_llm`
- `src/luckbot/plugins/builtin/session_plugin/__init__.py`
  - `SessionPlugin._before_llm`
- `src/luckbot/entrypoints/cli.py`
  - `async_main`
  - `_run_single_prompt`

## 边界

- agent loop 负责执行骨架、hook 串联、模型调用与工具回接
- agent loop 不负责定义业务工具、记忆语义、session 存储格式或 skill 内容
- `agent_loop()` 负责完整 run 生命周期，`run_react_loop()` 只负责可复用的 ReAct 子循环
- system prompt 当前是调用时临时拼到最前面的 `SystemMessage`，不属于返回消息链的一部分
- 工具异常大多被归一化为文本回灌模型，而不是直接终止整个 run

## 风险

- hook 顺序仍是运行时语义的一部分；虽然关键内置插件已通过 `dependencies` 显式约束顺序，但新增或调整依赖仍会直接改变 session、memory、skills、mcp 与 observability 看到的运行态
- 消息链一旦在 loop 层被整体替换，会同时影响模型上下文、session 落盘和后续多轮续聊；这是当前设计语义，不是局部副作用
- 工具当前按并发方式执行；若工具或插件共享可变状态且缺少隔离，行为可能依赖并发时序，且 `ToolMessage` 顺序不代表真实完成先后
- `after_run` 当前走 finally 路径；异常退出时也会执行，但此时 `result` 可能为空、`messages` 可能只是部分链，依赖该 hook 的插件不能假设 run 正常完成
