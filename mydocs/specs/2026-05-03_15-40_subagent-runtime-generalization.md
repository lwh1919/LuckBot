# Spec

## Task

- 将当前 memory flush 子 agent 抽象为 LuckBot 的通用 subagent 运行模块，并把记忆压缩场景改为通过权限、功能注入与提示词组装来配置

## Task Understanding

- 当前 LuckBot 已经存在一个事实上的 subagent：`src/agent/memory/flush_agent.py`
- 但它不是通用模块，而是把受限工具、私有工具实现、专用 system prompt、独立 `PluginManager`、`run_react_loop()` 调用都揉在同一个 memory 文件里
- 这导致：
  - subagent runtime 与业务场景耦合
  - memory flush 的权限边界与能力边界只能写死在 memory 模块里
  - 后续如果要新增别的 subagent（例如摘要、审查、整理、专项写入），很可能继续复制同类胶水代码
- 本轮需要先把 runtime 抽象出来，让 memory flush 从“专用实现”转成“通用 subagent runtime + memory flush 配置/能力装配”的第一位使用者

## Goal

- 建立一个可复用的 subagent 通用运行模块
- 让 memory flush 改为该模块的配置化实例，而不是继续直接拼装 `PluginManager + tools + prompt + run_react_loop`
- 明确 subagent 的三个核心抽象面：
  - 权限/能力注入
  - 提示词组装
  - 执行入口与返回约定

## Scope

- `src/agent/memory/flush_agent.py`
- 新增或调整 `src/agent/subagent/` 相关运行时模块
- 可能涉及 `src/agent/plugin/manager.py`
- 可能涉及 `src/agent/loop/agent_loop.py`
- 相关测试
- `mydocs/project/agent-loop.md`
- `mydocs/project/plugin-system.md`
- `mydocs/project/memory-system.md`

## Constraints

- 当前轮次尚未收到 `Plan Approved` 或 `批准执行`
- 在收到批准前，不进入业务代码修改
- 不改变主 `agent_loop()` 的既有外部调用语义
- 不破坏当前 memory compaction 的触发条件、递归防护与 transcript 压缩联动
- 抽象应优先服务“受限子运行时”这一能力边界，而不是为了未来场景过度设计

## Approval Status

- `Executed`

## Research Findings

- 任务开始时，真正的 subagent 入口只有 `src/agent/memory/flush_agent.py`
- 任务开始时，`run_memory_flush_agent()` 的核心流程是：
  - 用 `MemoryFlushToolsPlugin` 注册白名单工具
  - 创建独立 `PluginManager`
  - 手工拼接 `MEMORY_FLUSH_AGENT_SYSTEM`
  - 手工拼接本轮 transcript user message
  - 直接调用 `run_react_loop()`
  - 结束后执行 `index.sync()`
- 当前 memory flush 的“子 agent 语义”分散在多个层：
  - `compaction.py` 决定何时触发、是否嵌套、防递归与 max steps
  - `flush_agent.py` 决定工具白名单、提示词、输入消息形态、后处理
  - `PluginManager` 只是运行时装配器，不理解 subagent 概念
  - `run_react_loop()` 只提供 ReAct 子循环，不理解权限、角色、任务模板
- 当前插件系统已具备“子任务独立 manager”的事实能力，但缺一个上层 runtime 抽象
- `PluginContext` 当前已经能承载 subagent 所需的最小装配面：
  - `register_tool()`
  - `register_hook()`
  - `register_service()`
  - 因而 subagent 不一定需要发明新的插件协议，更适合复用现有 `PluginManager`
- `run_react_loop()` 当前已经是天然的 subagent 执行内核：
  - 不跑 `before_run` / `after_run`
  - 但支持 `before_llm_call` / `after_llm_call` / tool hooks
  - 适合做“受限工具 + 专用 prompt + 指定 messages”的短子运行时
- memory flush 当前至少包含三类不同层次的逻辑：
  - 通用运行时逻辑：独立 `PluginManager`、tool snapshot、`run_react_loop()` 调用、销毁
  - 可配置任务逻辑：system prompt 基础约束、user prompt 组装、最大步数、返回消息
  - memory 业务逻辑：读写白名单、默认 `memory/<day>.md`、`NO_MEMORIES_TOKEN`、结束后 `index.sync()`
- 当前项目真相文档中已明确：
  - `run_react_loop()` 是可复用子循环入口
  - 插件系统可服务主 loop 和子任务 loop
  - memory flush 是一个受限工具集的短 ReAct 子循环
- 当前测试中尚未看到专门覆盖 `flush_agent` / `subagent runtime` 的专项测试

## Plan

- 第一步：新增 `src/agent/subagent/` 模块
  - 定义 subagent spec / request / result 的最小数据结构
  - 提供统一执行入口，例如 `run_subagent(...)`
  - 该入口内部负责：
    - 创建独立 `PluginManager`
    - 装配 capability plugins
    - 组装 prompt 与初始 messages
    - 调用 `run_react_loop()`
    - 负责销毁 manager
- 第二步：把 memory flush 的工具能力拆成 memory 场景能力提供器
  - 保留 memory 自己的读写白名单与业务工具工厂
  - 但不再让 memory 文件自己承担“运行时搭建”职责
- 第三步：把 prompt 组装拆成两层
  - subagent runtime 负责基础角色/约束拼装接口
  - memory flush 负责提供任务模板和动态参数（如 `day`、transcript）
- 第四步：让 `run_memory_flush_agent()` 退化为 memory 领域适配层
  - 输入仍保持现有 `transcript / memory_paths / index / provider / max_steps / day`
  - 内部改为构造 memory flush subagent spec，再交给通用 runtime 执行
  - `index.sync()` 这类 memory 后处理仍保留在 memory 侧
- 第五步：补测试
  - 新增 subagent runtime 单测
  - 新增 memory flush 迁移后的行为单测
  - 必要时补 compaction 侧集成测试，证明调用链未断
- 第六步：回写 `project/agent-loop.md`、`project/plugin-system.md`、`project/memory-system.md`

## Risk

- 若 runtime 抽象落点过低，只是把现有代码搬家，扩展性问题不会实质改善
- 若抽象落点过高，容易把尚未存在的多种 subagent 场景提前泛化，反而增加复杂度
- memory flush 目前带有业务后处理（`index.sync()`、默认日文件规则、无写入 token），需要区分哪些属于通用 runtime，哪些仍属于 memory 场景

## Done Contract

- 代码中存在独立的 subagent 通用运行模块，而不是只有 memory 私有实现
- memory flush 改为通过通用模块装配能力、权限和 prompt，而不是自行拼装 runtime
- memory flush 现有对外行为保持可用：
  - 仍由 compaction 触发
  - 仍受白名单工具限制
  - 仍使用默认日文件策略
  - 仍在结束后同步 memory index
- 有测试或等价证据证明 runtime 抽象和 memory flush 迁移没有破坏主链路
- 相关真相文档已回写

## Validation

- 代码级核对 subagent 抽象边界与 memory 使用方式
- 运行与 subagent / memory flush 直接相关的 pytest 用例
- 人工检查 memory compaction → subagent → sync 的调用链是否仍闭合
- 已执行：`pytest -q src/tests/test_subagent_runtime.py src/tests/test_plugin_services.py`
- 结果：`13 passed`

## Next Actions

- 继续补代码导航，确认最合适的抽象落点与文件拆分
- 产出可执行 plan，等待你的批准词

## Change Log

- 新增 `src/agent/subagent/` 通用 runtime，提供 `run_subagent()`、request/result 数据结构以及 plugin/provider 双入口
- `run_memory_flush_agent()` 改为 memory 领域适配层：负责 prompt 组装、memory flush plugin 装配、trace 输出和 `index.sync()`
- 新增 `src/tests/test_subagent_runtime.py`，覆盖 plugin-only、provider 注入、异常销毁与 memory flush runtime 委托
- 回写 `project/agent-loop.md`、`project/plugin-system.md`、`project/memory-system.md`
- 定向测试通过：`13 passed`

## Resume or Handoff

- 若下一轮继续，可在通用 runtime 之上继续抽象更多 subagent 场景；首选继续复用 plugin-first 入口，仅在轻量场景使用 provider 入口
