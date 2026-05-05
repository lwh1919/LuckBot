# Spec

## Task

- 将主 agent 与 subagent 收束为统一的 agent runtime 抽象

## Task Understanding

- 当前主 agent 与 subagent 共享的本体已经是：
  - `PluginManager`
  - hook / tools / services
  - `run_react_loop()`
- 当前两者的主要差异不是执行内核，而是运行时 profile：
  - 插件集合
  - prompt 组装
  - 工具与 service 白名单
  - 是否启用 `before_run / after_run`
  - `PluginManager` 生命周期
- 当前还存在一个关键真实差异：
  - 主 agent 由 CLI 复用一个长期存在的 `PluginManager`
  - subagent 当前是按次创建和销毁独立 `PluginManager`

## Goal

- 建立一个统一的 runtime core
- 让主 agent 与 subagent 都成为该 runtime 的薄适配层
- 将差异收束为 runtime spec / profile / manager lifecycle，而不是分散在两套入口代码里

## Scope

- `src/agent/loop/agent_loop.py`
- `src/agent/subagent/runtime.py`
- 新增或调整 `src/agent/runtime/`
- `src/app/cli.py`
- `src/agent/memory/flush_agent.py`
- 相关测试
- 相关项目真相文档

## Constraints

- 已有用户批准词：`Plan Approved`
- 本轮允许进入代码实现
- 尽量保留现有外部调用面，但内部统一到单一 runtime core
- 不破坏现有主 loop 行为、memory flush 行为、session / tracer / skills 联动语义

## Approval Status

- `Executed and Validated`

## Research Findings

- `agent_loop()` 当前负责：
  - remote skill 文本规范化
  - `before_run`
  - `conversation_history + HumanMessage(user_input)` 初始链构造
  - `after_run`
- `run_react_loop()` 当前已经是主 agent 与 subagent 共享的 LLM/tool 交替内核
- `run_subagent()` 当前本质是：
  - 创建独立 `PluginManager`
  - 装配 plugins / providers
  - 直接调用 `run_react_loop()`
  - 销毁 manager
- CLI 当前复用一个长期存活的 `PluginManager`，并且 slash 指令依赖其中的 service
- 因此统一 runtime 需要同时支持：
  - 复用外部 `PluginManager`
  - 内部拥有并管理 `PluginManager`

## Plan

- 第一步：新增统一 runtime core
  - 定义 `RuntimeSpec`
  - 定义 `RuntimeProfile`
  - 定义 `RuntimeResult`
  - 提供 `run_runtime()` 统一入口
- 第二步：把 `run_react_loop()` 下沉到 runtime 层
  - 主 agent 与 subagent 都从 runtime 层使用同一执行内核
- 第三步：把 `agent_loop()` 改成主 agent 适配层
  - 继续接受当前参数形状
  - 内部改为构造 runtime spec 并委托给 `run_runtime()`
- 第四步：把 `run_subagent()` 改成受限 profile 适配层
  - 保留当前 request/result 形状或其薄封装
  - 内部改为委托给 `run_runtime()`
- 第五步：让 memory flush 与相关测试迁移到统一 runtime
- 第六步：补 runtime 直测与主/子适配层回归测试
- 第七步：回写真相文档

## Risk

- 若 runtime core 对 `PluginManager` 生命周期建模不清，会破坏 CLI 会话级 service 语义
- 若迁移时把主 agent 的 `before_run / after_run` 语义错误带入 subagent，会污染受限运行时
- 若文档不回写，会继续保留“主 loop / subagent 是两套体系”的过时真相

## Done Contract

- 仓内存在单一 runtime core，主 agent 与 subagent 都委托给它
- 主 agent 与 subagent 的差异收束为 runtime spec / profile
- CLI 主链路仍可用
- memory flush 仍可用
- 相关测试通过
- 真相文档已回写

## Validation

- 定向 runtime / agent_loop / subagent / memory flush 测试
- `src/tests` 全量回归
- 文档与代码一致性核对
- 已执行：
  - `pytest src/tests/test_agent_runtime.py -q`
  - `pytest src/tests/test_subagent_runtime.py -q`
  - `pytest src/tests/test_plugin_services.py -q`
  - `pytest src/tests -q`
- 当前结果：
  - `24 passed`
  - 存在第三方依赖 warning，无失败

## Change Log

- 新增统一 runtime core：`src/agent/runtime/core.py`
- 新增 runtime 对外导出层：`src/agent/runtime/__init__.py`
- `agent_loop()` 改为主 agent 薄适配层，委托 `run_runtime()`
- `run_subagent()` 改为受限 profile 薄适配层，委托 `run_runtime()`
- 新增 `src/tests/test_agent_runtime.py`，覆盖 owned manager、external manager 与 spec 校验
- 调整 `test_subagent_runtime.py` 与 `test_plugin_services.py` 的 LLM monkeypatch 入口
- 新增 `project/runtime-system.md`
- 回写 `project/README.md`、`project/agent-loop.md`、`project/plugin-system.md`、`project/memory-system.md`
