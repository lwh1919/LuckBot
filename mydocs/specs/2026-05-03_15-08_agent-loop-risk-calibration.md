# Spec

> Historical note (2026-05-06): this review predates the observability rewrite and references the removed `tracer_plugin`. Current runtime observability is implemented via `src/agent/observability`.

## Task

- 审查 `project/agent-loop.md` 中 `风险` 一节，逐条确认风险是否真实、如何触发、造成什么后果，以及最终应改代码还是改文档

## Task Understanding

- 当前 `project/agent-loop.md` 已经描述了 agent loop 主体行为，但 `风险` 一节仍不够完整：
  - 有的风险只写了结论，没有写触发机制
  - 有的风险没有写后果传播范围
  - 有的风险没有区分“当前真实风险”和“条件性风险”
  - 有的风险没有写清最终应怎样处理
- 本轮首先是风险审查任务，不预设出口一定是改文档：
  - 如果风险来自实现缺陷或可被更稳实现消除，应优先改代码
  - 如果风险是当前有意保留的运行时语义或权衡，则应在真相文档中准确注明
  - 如果既存在实现改进空间又需要保留残余风险，则代码和文档都应更新
- 本轮要让 AI 和技术人员读 spec 就能知道：
  - 这条风险是否真实
  - 它是怎么被触发的
  - 它会影响什么
  - 它最终应该通过改代码消除、通过更优雅实现收敛，还是只能在文档中注明

## Goal

- 对 `agent-loop.md` 中 4 条风险逐条完成审查
- 为每条风险写清：
  - 是否真实
  - 如何触发
  - 会造成什么后果
  - 应如何处理：改代码 / 改实现 / 改文档 / 组合处理
- 让执行阶段不再临场决定处理方向

## Scope

- `mydocs/project/agent-loop.md`
- `src/agent/loop/agent_loop.py`
- `src/agent/built_in/__init__.py`
- `src/agent/built_in/session_plugin/__init__.py`
- 与风险判断直接相关的插件 hook 实现

## Constraints

- 本轮只校准 `project truth`，不扩写成架构设计文档
- 风险描述必须可直接映射到当前代码行为，不能靠推测
- 风险处理优先级是：
  - 能通过更稳实现消除的，优先改代码
  - 不能或不应在本轮通过实现消除的，再在文档中准确注明
- 未获批前，本轮可以完善 spec 与 research，但不能直接进入代码修改或文档落地
- 未收到 `Plan Approved` 或 `批准执行` 前，不修改 `project/agent-loop.md`

## Approval Status

- `Executed`

## Research Findings

- 风险 1：`before_run`、`before_llm_call`、`before_tool_call` 的顺序变化会直接改变 session、memory、skills、mcp、tracer 的联动语义
  - 核实方法：
    - 检查内置插件发现顺序是否稳定
    - 检查 hook 是否按注册顺序执行
    - 检查是否有插件显式依赖“前一个插件已改写后的输入”
  - 已确认事实：
    - 内置插件按 `agent/built_in/*_plugin` 的目录名排序自动发现并初始化
    - hook 按注册顺序串联执行
    - `SessionPlugin._before_llm()` 的注释明确说明它依赖在 Memory 等插件之后运行，用来观察消息链是否被压缩变短
  - 触发方式：
    - 调整内置插件初始化顺序
    - 改变 hook 注册顺序
    - 在已有顺序依赖的前提下插入新的 hook 改写链路
  - 后果：
    - session 可能看不到 memory 压缩后的消息链
    - skills、mcp、tracer 看到的输入快照可能变化
    - 同一轮 run 的 prompt、tools、messages 语义发生联动漂移
  - 结论：
    - 该风险是真实风险，不是推测
  - 处理方向：
    - 已执行：通过插件 `dependencies` 将关键顺序从隐式目录顺序收紧为显式依赖顺序
    - 同时回写文档，说明顺序仍是运行时语义的一部分
- 风险 2：消息链一旦在 loop 层被改写，会同时影响模型上下文、session 落盘和后续多轮续聊
  - 核实方法：
    - 检查 `before_run` 是否可改写初始历史
    - 检查 `before_llm_call` 是否可整体替换 `messages`
    - 检查替换后的 `messages` 是否同时流向模型调用、`after_run` 与下一轮 `conversation_history`
  - 已确认事实：
    - `before_run` 会改写初始 `conversation_history`
    - `before_llm_call` 可以整体替换 `messages`
    - 替换后的 `messages` 同时进入模型输入、`after_run`、session transcript 和下一轮 `conversation_history`
  - 触发方式：
    - memory 压缩替换消息链
    - 未来新增插件在 `before_run` 或 `before_llm_call` 中重写消息链
  - 后果：
    - 模型看到的上下文被改变
    - session 落盘的 transcript 被改变
    - 下一轮续聊继承的历史被改变
    - 一次改写会沿着“模型输入 -> 持久化 -> 后续会话”连续传播
  - 结论：
    - 该风险是真实风险，且属于 loop 的核心语义
  - 处理方向：
    - 已决定本轮不收紧接口
    - 已回写文档，明确这是当前设计语义及其传播后果
- 风险 3：工具是并发执行的；若某些工具隐含共享状态，行为可能依赖并发时序
  - 核实方法：
    - 检查 tool call 是否并发执行
    - 检查 loop 是否保证结果写回顺序与执行顺序一致
    - 检查当前是否已经存在“共享可变状态 + 无隔离”的直接证据
  - 已确认事实：
    - tool call 通过 `asyncio.gather()` 并发执行
    - `ToolMessage` 追加顺序按原始 tool call 顺序，不按完成先后顺序
    - 当前尚未发现足以证明“现有工具一定已受并发时序影响”的直接证据
  - 触发方式：
    - 同一轮多个 tool call 并发执行
    - 工具或插件读写共享可变状态
    - 共享状态没有锁、事务或隔离边界
  - 后果：
    - 工具行为可能依赖完成先后顺序
    - 同样输入在不同调度下可能得到不同运行结果
    - 调试时看到的 `ToolMessage` 顺序不一定反映真实执行先后
  - 结论：
    - 该风险应保留，但应标注为条件性风险，而不是当前已发生的问题
  - 处理方向：
    - 已决定本轮不改并发模型
    - 已回写文档，明确其触发条件、后果与非 bug 性质
- 风险 4：`after_run` 不是 finally 语义；前面阶段抛异常时，依赖 `after_run` 的持久化与观测都不会执行
  - 核实方法：
    - 检查 `agent_loop()` 是否使用 `try/finally`
    - 检查哪些关键收尾动作依赖 `after_run`
  - 已确认事实：
    - `agent_loop()` 当前没有用 `try/finally` 包住 `run_react_loop()`
    - `session flush`、`memory sync`、`tracer` 收尾都依赖 `after_run`
  - 触发方式：
    - `before_run` 抛异常
    - `build_llm()` 抛异常
    - `before_llm_call` 抛异常
    - 模型调用过程抛异常
  - 后果：
    - transcript 不会 flush
    - memory 不会 sync
    - tracer 不会完成本轮收尾
    - 异常路径下可观测性与持久化状态都可能缺失
  - 结论：
    - 该风险是真实风险，且应明确为“只在正常返回路径执行 `after_run`”
  - 处理方向：
    - 已执行：将 `after_run` 收尾改为 finally 路径，并向 hook 暴露 `error`
    - 同时回写文档，说明异常路径下 `result` 与 `messages` 的语义

## Plan

- 风险 1：
  - 改代码 + 改文档
  - 用插件 `dependencies` 显式约束 `memory -> session`、`skills -> remote skill`、`... -> tracer`
- 风险 2：
  - 仅改文档
  - 保留 loop 层整体替换消息链的能力，明确其传播后果
- 风险 3：
  - 仅改文档
  - 保留并发模型，明确共享状态才是风险触发条件
- 风险 4：
  - 改代码 + 改文档
  - 将 `after_run` 放入 finally 路径，并把异常对象传给 hook

## Risk

- 若过早假设“只能改文档”，会把应修复的实现缺陷错误固化成长期真相
- 若把所有风险都升级为代码改造任务，会把“真实约束”与“真实 bug”混淆
- 若补充上下文过多，会让 `project truth` 退化成实现细节堆积

## Done Contract

- `agent-loop.md` 的 4 条风险都能回答：
  - 是否真实
  - 如何触发
  - 会造成什么后果
  - 应如何处理：改代码 / 改实现 / 改文档 / 组合处理
- 对每条风险，都有明确处理结论，而不是默认落到文档修订
- 若存在应修复的 bug 或明显更优实现，计划中会显式包含代码修改
- 文档能让读者区分“当前硬事实”“条件性风险”“已决定保留的实现约束”

## Validation

- 风险 1 校验：
  - 对照插件发现顺序、hook 执行顺序、`SessionPlugin` 注释
  - 结果：已通过
  - 说明：关键内置插件顺序已由显式依赖约束，不再只依赖目录顺序
- 风险 2 校验：
  - 对照 `before_run`、`before_llm_call`、`after_run`、返回 `messages`
  - 结果：已通过
  - 说明：已明确保留该语义，并回写传播路径
- 风险 3 校验：
  - 对照 `asyncio.gather()` 与结果回写顺序
  - 结果：已通过
  - 说明：当前没有直接证据表明现有工具已构成并发 bug
- 风险 4 校验：
  - 对照 `agent_loop()` 是否缺少 `try/finally`，以及 `after_run` 承担的收尾动作
  - 结果：已通过
  - 说明：`after_run` 已在正常/异常退出路径执行，异常对象会回传给 hook
- 全文校验：
  - 风险段保持精悍
  - 每条风险都覆盖四个问题
  - 结论不超出代码事实
  - 处理方向不预设为“只改文档”
  - 自动化验证：
    - `pytest -q LuckBot/src/tests/test_plugin_services.py` 通过，`9 passed`

## Next Actions

- 若后续继续收紧插件系统，可再评估是否需要引入 hook phase / priority 模型
- 若后续出现共享状态工具，再决定是否对并发模型追加隔离约束

## Change Log

- `src/agent/loop/agent_loop.py`
  - 将 `after_run` 收尾放入 finally 路径
  - 异常退出时仍执行 `after_run`
  - 将 remote skill 预处理纳入同一异常收尾路径
- `src/agent/plugin/hooks.py`
  - `AfterRunInput` 新增 `error`
- `src/agent/built_in/session_plugin/__init__.py`
  - 增加 `dependencies = ["memory"]`
- `src/agent/built_in/tracer_plugin/__init__.py`
  - 增加显式依赖
  - 支持异常退出 trace 收尾
- `src/agent/built_in/tracer_plugin/collector.py`
  - 记录 run 是否失败及异常摘要
- `src/agent/built_in/tracer_plugin/renderer.py`
  - 渲染 run success / failure 状态
- `src/tests/test_plugin_services.py`
  - 新增插件依赖顺序测试
  - 新增 `after_run` 在异常路径仍执行的测试
- `mydocs/project/agent-loop.md`
  - 回写 hook 顺序来源、`after_run` finally 语义与新的风险表述

## Resume or Handoff

- 当前任务已完成
- 后续若继续处理 agent loop 风险，优先看：
  - 是否需要给 hook 引入更细粒度 phase 模型
  - 是否需要为共享状态工具增加并发隔离约束
