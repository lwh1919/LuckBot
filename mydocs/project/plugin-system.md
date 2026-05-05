# Plugin System Truth

## 文件定位

`project/plugin-system.md` 记录 LuckBot 插件系统当前已经实现的真实行为。

本文当前边界是：

- 插件系统负责装配工具、hook 与 service，不承载具体业务语义
- `memory`、`session`、`skills`、`mcp`、`tools` 等运行时能力当前都通过插件接入
- agent loop 负责调用插件 hook，插件系统本身不执行 LLM 推理

## 模块职责

LuckBot 当前的插件系统负责：

- 定义插件基类与初始化契约
- 提供共享的 `PluginContext` 注册表
- 扫描内置插件与外部插件目录
- 按依赖关系对插件做拓扑排序
- 初始化并销毁插件实例
- 为运行时暴露 `tools`、`hooks`、`services`
- 让 `agent_loop` 与子任务 loop 按统一顺序调用插件 hook

## 当前真实行为

### 1. 插件契约当前由 `LuckbotPlugin` 与 `PluginContext` 定义

`src/luckbot/core/plugin/base.py` 是插件系统的基础契约层。

当前每个插件都需要继承 `LuckbotPlugin`，并具备这些类属性：

- `name`
- `version`
- `dependencies`

当前必须实现的方法只有：

- `initialize(ctx)`

`destroy(ctx)` 当前是可选异步方法，默认什么都不做。

`PluginContext` 是插件初始化阶段拿到的共享注册表。

它当前维护三类容器：

- `_tools`
- `_hooks`
- `_services`

插件初始化时只能通过 `PluginContext` 暴露能力，而不是直接改 `PluginManager` 内部结构。

### 2. 当前 `PluginContext` 负责注册工具、hook 与 service

`PluginContext` 当前提供：

- `register_tool(name, tool)`
- `get_tools()`
- `register_hook(hook_name, handler)`
- `get_hooks(hook_name)`
- `register_service(name, service)`
- `get_service(name)`

当前工具注册语义是：

- 工具名重复时不会报错
- 会记录 warning
- 后注册的工具覆盖前面的同名工具

当前 service 注册语义也是：

- 名称重复时直接抛 `RuntimeError`
- 不允许通过后注册覆盖前面的同名 service

当前 hook 注册语义不同：

- hook 名必须属于 `VALID_HOOK_NAMES`
- 非法名称直接抛 `ValueError`
- 合法 hook 按注册顺序 `append` 到列表
- 不做去重与覆盖

`get_tools()` 当前返回字典副本，`get_hooks()` 返回列表副本。

这意味着：

- 调用方可以拿到当前快照
- 但不会直接拿到 `PluginContext` 内部容器引用

`get_service()` 当前直接返回注册对象本身，不做复制。

### 3. 当前只有六类合法 hook

合法 hook 名定义在 `base.py` 的 `VALID_HOOK_NAMES`：

- `before_run`
- `after_run`
- `before_llm_call`
- `after_llm_call`
- `before_tool_call`
- `after_tool_call`

`src/luckbot/core/plugin/hooks.py` 定义了这些 hook 的入参与返回值结构。

当前语义是：

- `before_run`
  - 面向单次 `agent_loop()` 调用
  - 可改写 `tools`
  - 可改写 `system_prompt`
  - 可整体替换 `conversation_history`
  - 还能收到 `remote_skill`
- `after_run`
  - 面向单次 run 结束
  - 能拿到最终文本与完整消息链
- `before_llm_call`
  - 面向每轮 ReAct 迭代
  - 可改写 `tools`
  - 可改写 `system_prompt`
  - 可改写当前 `messages`
- `after_llm_call`
  - 面向每轮模型返回
  - 能拿到响应对象与 usage
- `before_tool_call`
  - 面向每次工具调用
  - 可改写工具入参
  - 抛异常可中止调用
- `after_tool_call`
  - 面向每次工具执行完成
  - 可改写工具输出文本

### 4. 当前 `PluginManager` 是插件装配与生命周期主入口

`src/luckbot/core/plugin/manager.py` 中的 `PluginManager` 负责持有：

- `_ctx`
- `_plugins`
- `_initialised`

`initialize()` 当前行为是：

1. 若 `_initialised` 已为真，直接抛异常，要求调用方先 `destroy()`
2. 收集显式传入的 `plugins`
3. 如有 `plugin_dirs`，逐个扫描外部插件目录
4. 对合并后的插件实例做拓扑排序
5. 按排序结果依次执行 `plugin.initialize(self._ctx)`
6. 把已初始化实例追加到 `_plugins`
7. 设置 `_initialised = True`

`destroy()` 当前行为是：

- 按 `_plugins` 的逆序逐个调用 `destroy()`
- 单个插件销毁失败只记日志，不中断后续销毁
- 最后清空 `_plugins`
- 最后重置 `PluginContext`，清空 `tools`、`hooks`、`services`
- 最后把 `_initialised` 复位为 `False`

所以当前一个 `PluginManager` 实例的真实用法是：

- 初始化一次
- 使用
- 销毁
- 如有需要，可再次初始化并复用同一个 manager 实例
- 若仍处于已初始化状态，则不可直接重复 `initialize()`

### 5. 当前插件来源分成内置与外部两类

内置插件发现位于 `src/luckbot/plugins/builtin/__init__.py` 的 `discover_builtin_plugins()`。

当前规则是：

- 扫描 `src/luckbot/plugins/builtin/` 下所有 `*_plugin/` 目录
- 要求目录中存在 `__init__.py`
- 通过 `importlib.import_module("agent.built_in.<name>")` 导入
- 用 `_find_plugin_class()` 找到第一个 `LuckbotPlugin` 子类
- 成功后直接实例化并加入结果列表

外部插件扫描位于 `PluginManager._scan_dir()`。

当前规则是：

- 调用方传入外部目录，例如 CLI 中的 `.luckbot/plugins`
- `.luckbot/plugins` 当前语义是“项目工作区本地私有插件层”
- 它用于当前工作区的个人插件注入与覆盖，默认不作为仓库分发目录
- 仅扫描该目录下一级的 `*_plugin/` 包
- 要求目录中存在 `__init__.py`
- 通过 `_load_plugin_package()` 动态导入

`_load_plugin_package()` 当前会：

- 先把父目录加入 `sys.path`
- 再按包名导入
- 再找第一个 `LuckbotPlugin` 子类并实例化

### 6. 当前插件依赖通过名字做拓扑排序

插件依赖排序由 `PluginManager._topo_sort()` 完成。

当前实现按 `plugin.name` 建立依赖图，并使用 DFS：

- `visited`
- `visiting`

当前行为是：

- 如果发现循环依赖，抛 `RuntimeError("检测到插件循环依赖")`
- 如果某个依赖名字在插件集合里不存在，抛 `RuntimeError("缺少依赖")`
- 排序结果保证依赖插件先初始化，依赖方后初始化

依赖判断只看：

- 插件类属性 `dependencies`

例如当前 `RemoteSkillPlugin.dependencies = ["luckbot/built-in-skills"]`，所以它会在 `SkillsPlugin` 之后初始化。

### 7. 当前 `agent_loop` 负责按顺序执行插件 hook

插件系统本身只负责注册和暴露 hook，真正的调用顺序在 `src/luckbot/core/runtime/agent_loop.py`。

`agent_loop()` 当前真实顺序是：

1. 取 `plugin_manager.get_tools()` 作为初始工具表
2. 处理内联 remote skill 指令并得到 `effective_remote_skill`
3. 依次执行所有 `before_run` hook
4. 把当前用户消息追加到 `messages`
5. 进入 `run_react_loop()`
6. run 结束后依次执行所有 `after_run` hook

`before_run` 的联动语义是：

- 前一个 hook 改写后的 `tools`
- `system_prompt`
- `conversation_history`

会作为后一个 hook 的输入继续向下传递。

### 8. 当前 ReAct 内循环继续复用插件系统

`run_react_loop()` 当前每轮都会：

1. 从固定 `tools` 和 `system_prompt` 启动本轮快照
2. 依次执行所有 `before_llm_call`
3. 用改写后的 prompt 与工具表调用 LLM
4. 依次执行所有 `after_llm_call`
5. 若模型返回 tool calls，则并发执行 `_run_one_tool()`

`_run_one_tool()` 当前每个工具调用的顺序是：

1. 依次执行所有 `before_tool_call`
2. 用改写后的参数执行工具
3. 依次执行所有 `after_tool_call`
4. 把最终输出包装成 `ToolMessage`

当前工具调用是并发的，但单个工具调用内部的 hook 仍按顺序串行执行。

### 9. 当前插件系统不只服务主对话，也服务子任务 loop

插件系统不是只给 CLI 主会话用。

`src/luckbot/core/runtime/core.py` 当前统一承载主 agent 与受限运行时的插件装配语义。

当前子任务链路是：

- `run_runtime()`
  - 若未传外部 `PluginManager`，则创建新的 `PluginManager`
  - 初始化显式传入的 runtime plugins
  - 若给了轻量 providers，再通过内部 runtime plugin 把 tools / services 注册到同一个 context
  - 调 `run_react_loop()`
  - 若 manager 由 runtime 持有，则在结束后销毁
- `run_subagent()`
  - 作为受限 profile 适配层，构造 subagent spec 后再调 `run_runtime()`
- `run_memory_flush_agent()`
  - 作为 memory 领域适配层，构造 memory flush 专用 plugins、prompt 与 messages
  - 再调用 `run_subagent()`

这说明当前插件系统的边界是：

- 它是一个通用运行时装配层
- 主 agent 与受限运行时都可以各自持有独立的 `PluginManager`
- 主 agent / subagent 语义本身不属于 `PluginManager`，而属于其上的 runtime 组装层

### 10. 当前 CLI 是插件系统的主要装配入口

`src/luckbot/entrypoints/cli.py` 与 `src/luckbot/adapters/gateway/service.py` 当前是两条主要装配路径：

- 本地 CLI
- gateway service

两者都会：

- 创建 `PluginManager()`
- 用 `discover_builtin_plugins()` 取内置插件
- 额外扫描 `.luckbot/plugins`
  - 该目录是项目工作区本地私有插件目录
- 在结束时调用 `pm.destroy()`

当前上层入口仍直接依赖插件系统暴露的 service 与观测接口，例如：

- `/plugin`
  - 通过 `pm.list_plugins()` 展示已加载插件
- `/mcp list`
  - 通过执行与标准 run 同顺序的 `before_run` 合并，观察 MCP 工具
- `/session save`
  - 通过 `pm.get_service("session_flush")` 调用 session 插件能力

所以当前 CLI 不知道每个业务模块的内部实现细节，而是通过插件系统统一取能力。

### 11. 当前 `agent.plugin` 包只是对外聚合层

`src/luckbot/core/plugin/__init__.py` 当前只做公开 API 聚合。

它导出的是：

- `LuckbotPlugin`
- `PluginContext`
- 六类 hook 的 input / result 数据类

当前这里不包含：

- 发现逻辑
- 拓扑排序逻辑
- 运行时调度逻辑

这些逻辑仍分别位于：

- `core/plugin/base.py`
- `core/plugin/manager.py`
- `core/runtime/agent_loop.py`

## 关键入口

- `src/luckbot/core/plugin/base.py`
  - `LuckbotPlugin`
  - `PluginContext`
  - `VALID_HOOK_NAMES`
- `src/luckbot/core/plugin/hooks.py`
  - `BeforeRunInput`
  - `BeforeRunResult`
  - `BeforeLLMCallInput`
  - `BeforeLLMCallResult`
  - `AfterLLMCallInput`
  - `BeforeToolCallInput`
  - `BeforeToolCallResult`
  - `AfterToolCallInput`
  - `AfterToolCallResult`
  - `AfterRunInput`
- `src/luckbot/core/plugin/manager.py`
  - `PluginManager.initialize`
  - `PluginManager.destroy`
  - `PluginManager._scan_dir`
  - `PluginManager._topo_sort`
  - `_load_plugin_package`
  - `_find_plugin_class`
- `src/luckbot/plugins/builtin/__init__.py`
  - `discover_builtin_plugins`
- `src/luckbot/core/runtime/agent_loop.py`
  - `agent_loop`
  - `run_react_loop`
  - `_run_one_tool`
- `src/luckbot/core/subagent/runtime.py`
  - `run_subagent`
- `src/luckbot/domains/memory/flush_agent.py`
  - `MemoryFlushToolsPlugin`
  - `run_memory_flush_agent`

## 边界

- 插件系统负责装配、依赖排序、注册表与 hook 暴露
- 插件系统不负责 memory、session、skills、mcp 等模块自身的业务语义
- hook 的真实执行时机定义在 `agent_loop` / `run_react_loop`，不在 `PluginManager`
- 外部插件能接入 LuckBot，但必须遵守 `*_plugin/` 包结构和 `LuckbotPlugin` 契约
- service 是插件间与 CLI 的共享能力入口，不是对模型暴露的 tool

## 风险

- 工具重名当前仍只打 warning 并覆盖，插件间仍可能静默改变工具行为
- service 重名当前会直接失败；外部插件若想扩展能力，必须避免与现有 service 名冲突
- 插件名冲突当前没有显式拒绝；依赖排序按 `plugin.name` 建图，重名插件会产生不直观结果
- 若某个插件自身没有正确实现“destroy 后可再次 initialize”的内部状态收敛，同一个 manager 实例虽可复用，仍可能暴露插件级状态残留问题
- hook 按注册顺序串行生效，跨插件改写 `tools`、`prompt`、`messages` 时容易出现顺序耦合
