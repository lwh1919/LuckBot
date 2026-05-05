# Spec

> Historical note (2026-05-06): this audit references the removed `tracer_plugin` UI/collection path. Current runtime observability is implemented via `src/agent/observability` plus Grafana and LangSmith.

## Task

- 审计 LuckBot 整个 agent loop 中 `messages` 数据结构的生命周期，并把后续修复重心收束为两类问题：
- `tracer` 展示语义修正
- `compaction` 摘要持久化语义修正

## Task Understanding

- 当前需要沿 `agent_loop -> run_react_loop -> plugins -> session transcript -> tracer` 追踪 `messages`
- 目标不是先改代码，而是先确认：
  - `messages` 在各阶段的真实语义
  - 哪些模块会改写、追加、删除或只观测消息
  - 现在看到的 trace / token / prompt 展示，哪些是消息真问题，哪些只是 tracer 的展示口径
- 当前审计结论已经表明：
  - 主 `messages` 链路整体正常
  - 用户感知上的主要困惑来自 tracer 展示
  - 系统内部真实需要补齐的是 compaction 摘要跨轮语义

## Goal

- 给出一份以事实为准的 `messages` 生命周期审计结果
- 明确当前问题不应再泛化为“整个消息链异常”
- 将后续实现目标聚焦到：
- `tracer` 要把 prompt 注入和 message 变化区分清楚
- `compaction` 要解决摘要在运行态与持久化态之间的语义断层

## Scope

- `src/agent/loop/agent_loop.py`
- `src/agent/built_in/session_plugin/__init__.py`
- `src/agent/session/transcript.py`
- `src/agent/built_in/memory_plugin/__init__.py`
- `src/agent/memory/compaction.py`
- `src/agent/built_in/skills_plugin/__init__.py`
- `src/agent/built_in/tracer_plugin/*`
- 相关 `mydocs/project/*.md`

## Primary Focus

- Focus 1: `tracer` 展示层语义修正
- Focus 2: `compaction` 摘要持久化语义修正

## Non-Goals

- 不把本任务扩展成主 ReAct loop 的大重构
- 不把技能列表 prompt 注入机制本身认定为 bug
- 不默认重写整个 session transcript 格式

## Constraints

- 本轮目标是审计与判断，不默认进入代码实现
- 若发现问题，先给事实与计划，不直接改代码
- 不主动运行高成本命令；必要时仅做定向、低成本验证

## Approval Status

- `Executed and Validated`

## Research Findings

- `agent_loop()` 以 `conversation_history` 为基底，追加本轮 `HumanMessage` 后交给 `run_react_loop()`
- `run_react_loop()` 会原地追加 `AIMessage` 与 `ToolMessage`
- `before_llm_call` 是唯一会整体替换 `messages` 的 hook 入口
- `SessionPlugin` 只负责恢复、落盘与“压缩后是否需要重写 transcript”的判定
- `TracerPlugin` 自己不持有消息链副本，而是在 hooks 上被动采样 prompt、tool call 与 response 概览
- `SkillsPlugin` 不改 `messages`，只在 `before_llm_call` 把技能目录、已加载技能、已选文档拼进 `system_prompt`
- `MemoryPlugin` 在 `before_run` 向 `system_prompt` 注入 `MEMORY_RECALL`；在 `before_llm_call` 里可能触发 compaction
- `memory/compaction.py` 是当前唯一会在运行时真正“裁剪并替换 messages 链”的内置逻辑
- `Session transcript` 明确不序列化常规 `SystemMessage`，持久化只覆盖 `HumanMessage / AIMessage / ToolMessage`
- `Tracer renderer` 中的 `Prompt +N chars injected` 展示的是 `system_prompt` 增量，不是 `messages` 增量

## Message Lifecycle Audit

### Stage A. Run 输入

- CLI 把上轮返回的 `conversation_history` 保存在内存里，下一轮再次传入 `agent_loop()`
- 新进程恢复时，`SessionPlugin.before_run()` 才会从 `sessions/*.jsonl` 读回历史消息
- 这里的历史消息天然不含常规顶层 `SystemMessage`

### Stage B. before_run

- `MemoryPlugin.before_run()`：只改 `system_prompt`
- `SessionPlugin.before_run()`：必要时整体替换 `conversation_history`
- 此阶段尚未构造本轮 `messages`

### Stage C. 进入主 ReAct

- `agent_loop()` 在 `before_run` 结束后执行：
  - `messages = list(conv_history)`
  - `messages.append(HumanMessage(content=normalized_user_input))`
- 到这里为止，`messages` 的语义是：
  - 历史 user/assistant/tool 消息
  - 加上当前轮 user 消息

### Stage D. before_llm_call

- hook 顺序由插件拓扑排序 + 注册顺序决定，`memory -> session -> tracer`
- `MemoryPlugin.before_llm_call()` 可能返回新的 `messages`
- `run_react_loop()` 收到 `BeforeLLMCallResult(messages=...)` 后会：
  - `messages.clear()`
  - `messages.extend(result.messages)`
- `SessionPlugin.before_llm_call()` 只检测长度是否缩短，用于后续 transcript rewrite
- `TracerPlugin.before_llm_call()` 只记录 `system_prompt`，不记录 `messages` 内容

### Stage E. LLM / Tool 迭代

- 真正发给模型的是：
  - `[SystemMessage(content=current_prompt), *messages]`
- 顶层 prompt 不进入 `messages`
- 每轮 LLM 返回后：
  - 追加一条 `AIMessage`
  - 若有 tool calls，再逐条追加对应 `ToolMessage`

### Stage F. after_run / 持久化

- `SessionPlugin.after_run()` 把当前内存 `messages` 同步到 JSONL
- 常规情况下是从 `persisted_len` 开始增量追加
- 若 compaction 导致链变短，则整文件 rewrite 为当前内存快照
- transcript 读取恢复时，只能恢复 `HumanMessage / AIMessage / ToolMessage`

## Findings

### Finding 1

- 你看到的 `Prompt +596 chars injected` 重复出现，主体是 tracer 展示口径，不是 `messages` 链重复
- 原因：
  - 该面板来自 `TracerPlugin` 对 `system_prompt` 的 diff 展示
  - `SkillsPlugin` 每轮都会把技能列表重新拼进 `system_prompt`
  - tracer 只看 prompt hash / diff，不看 `messages`
- 结论：
  - 这块更像“展示语义一般”，不是消息结构异常
  - 后续修复应落在 tracer 采集/渲染层，不应误伤主 loop 的 message lifecycle

### Finding 2

- 当前运行态 `messages` 与持久化 transcript 的语义并不完全一致，这是设计上就存在的一层错位
- 原因：
  - 正常顶层 `system_prompt` 永远不进 `messages`
  - transcript 也明确不存 `SystemMessage`
  - 但 compaction 摘要会把一条 `SystemMessage(summary)` 插进 `messages`
- 影响：
  - 该摘要在当轮运行时有效
  - 一旦 session rewrite 到磁盘，这条 `SystemMessage(summary)` 会被 transcript 丢弃
  - 下一进程恢复后，压缩摘要不会回来
- 结论：
  - 这不是“多消息”或“少消息”导致当前 trace 误读的主因
  - 但它确实是一个真实的语义不对齐点，后续若追求严格一致性，应单独修
  - 后续修复应落在 compaction / transcript 语义边界，而不是 tracer

### Finding 3

- 主 loop 的消息增减规则本身是收敛的，未发现常规路径下“无故多一条/少一条”的证据
- 真实会改 `messages` 的地方很少：
  - 进入 loop 时追加当前 `HumanMessage`
  - LLM 后追加 `AIMessage`
  - tool 后追加 `ToolMessage`
  - compaction 时整体替换为“摘要 + recent tail”或仅 recent tail
- 其他插件多数只改 `system_prompt` 或只观测事件

## Validation Notes

- 测试已覆盖部分关键语义：
  - `test_builtin_plugin_dependencies_make_runtime_order_explicit()` 证明 `memory/session/tracer` 先后顺序是显式约束
  - `test_agent_loop_runs_after_run_even_when_build_llm_fails()` 与 `test_agent_loop_runs_after_run_even_when_before_llm_fails()` 证明异常路径下 `messages` 至少保留到“当前 user 已入链”的状态
  - `test_run_subagent_runs_with_plugins_only()` 与相关 subagent 测试证明 `run_react_loop()` 的追加语义在 subagent runtime 中也复用一致
- 当前缺口：
  - 缺少针对 `compaction -> session rewrite -> reload` 的语义回归测试
  - 缺少针对 tracer “prompt diff 不是 message diff”的展示级测试

## Audit Conclusion

- 结论一：你截图里反复出现的 `Prompt +596 chars injected`，主要是 tracer 展示 `system_prompt` 注入的结果，不能据此判断 `messages` 重复
- 结论二：主 loop 的 `messages` 生命周期整体是正常的，常规路径没有看到明显“多余消息”或“缺失消息”
- 结论三：真正值得修的是“运行态 compaction 摘要消息 vs transcript 不持久化”的语义错位，以及 tracer 文案容易把 prompt 注入误读成消息注入

## Reframed Problem Statement

- 当前任务的更准确表述是：
- `messages` 主链路基本正常
- 用户可见问题主要是 tracer 展示语义不精确
- 系统内部真实问题主要是 compaction 摘要的跨轮持久化语义不完整
- 因此，后续实现应优先修复：
- 展示层误导
- 摘要层持久化断层

## Plan

- 实施范围分为 3 个工作包：
- Workstream 1: `tracer` 展示语义修正
- Workstream 2: `compaction` 摘要持久化语义修正
- Workstream 3: 回归测试与验证

### Workstream 1: tracer 展示语义修正

- 目标：
  - 不再让 `Prompt +N chars injected` 被误读成“messages 重复注入”
  - 让 trace 明确区分 prompt 变化与 message 链变化
- 拟改文件：
  - `src/agent/built_in/tracer_plugin/collector.py`
  - `src/agent/built_in/tracer_plugin/renderer.py`
  - 如有必要：`src/agent/built_in/tracer_plugin/__init__.py`
- 具体改动：
  - 在 `before_llm_call` 采集阶段新增 message 维度观测
  - 至少记录：
    - 当前 `messages` 条数
    - 相比上一步是否发生替换、缩短或增长
    - 可选的尾部角色摘要
  - 调整现有 trace 文案，明确区分：
    - `system prompt changed`
    - `prompt appendix changed`
    - `messages changed`
- 设计约束：
  - tracer 不保存完整消息正文
  - 不改主 loop 业务语义，只改观测和渲染

### Workstream 2: compaction 摘要持久化语义修正

- 目标：
  - 消除“运行态看得到摘要、重启恢复后摘要消失”的语义断层
- 拟改文件：
  - `src/agent/memory/compaction.py`
  - `src/agent/session/transcript.py`
  - `src/agent/built_in/session_plugin/__init__.py`
  - 如有必要：相关 `mydocs/project/*.md`
- 推荐方向：
  - 优先把 compaction 摘要从临时 `SystemMessage` 改成可持久化表达
- 具体改动目标：
  - transcript 能识别并保存 compaction 摘要
  - reload 后能恢复出与运行时等价的摘要语义
  - 不影响普通顶层 `system_prompt` “不落盘”的原有规则
- 设计约束：
  - 必须区分“普通系统提示词”与“压缩摘要消息”
  - 不能把整个顶层 prompt 直接持久化到 transcript

### Workstream 3: 回归测试与验证

- 拟改文件：
  - `src/tests/test_plugin_services.py`
  - 视实现而定，可能新增 tracer / transcript 定向测试
- 必补用例：
  - 仅 `system_prompt` 变化时，trace 不暗示 `messages changed`
  - compaction 替换消息链时，trace 能显示 message 维度变化
  - compaction 后若触发 transcript rewrite，reload 后摘要语义仍在
  - 普通 transcript 路径仍不保存常规顶层系统提示词

### 执行顺序

- 第 1 步：先定 compaction 摘要的可持久化表达
- 第 2 步：落 transcript 序列化/反序列化与 session rewrite 适配
- 第 3 步：再改 tracer 采集与渲染
- 第 4 步：补回归测试
- 第 5 步：做定向验证

### Validation Plan

- 静态验证：
  - 检查主 loop 未被业务性改坏
  - 检查普通 transcript 路径仍只保存既定消息类型
- 单测验证：
  - 跑 tracer / session / compaction 相关定向 pytest
- 手工验证：
  - 复现一次 skills prompt 注入场景，确认 trace 不再像“消息重复注入”
  - 构造一次 compaction 场景，确认重启恢复后摘要语义仍存在或行为被明确定义

## Risk

- `messages` 的真实问题与 `system_prompt` 注入展示很容易混淆
- `trace` 面板展示的是 prompt diff 与工具摘要，不等于完整消息链
- `session` 持久化不保存 `SystemMessage`，与运行时看到的 prompt 视图存在天然错位

## Done Contract

- 能明确说清 `messages` 在每个阶段的增减与语义
- 能明确把“展示误读”与“真实语义断层”分开
- 能为后续两类修复给出清晰边界

## Validation

- 已完成代码级调用链审计
- 已完成实现后的定向与轻量全量验证：
  - `pytest src/tests/test_trace_and_transcript.py -q`
  - `pytest src/tests/test_plugin_services.py -q`
  - `pytest src/tests -q`
- 当前结果：
  - `21 passed`
  - 存在第三方依赖 warning，但无失败

## Next Actions

- 若要继续，可以做一轮真实 CLI 手工回放，观察 tracer 在 skills prompt 注入场景下的新展示
- 若后续再扩 tracer，可考虑补充 message role / count 的 richer UI，而不增加正文泄漏

## Change Log

- 新建本任务 spec，用于承载消息生命周期审计结论
- 回写 `messages` 生命周期审计、findings、验证缺口与结论
- 将任务重心收束到 `tracer 展示` 与 `compaction 摘要持久化`
- 发布详细实施计划，等待基于该计划的重新批准
- 已实现 compaction 摘要白名单持久化与恢复
- 已实现 tracer 的 prompt/message 双线观测与新文案
- 已新增 transcript / tracer 定向测试并通过回归
- 已回写 `project/session-system.md`、`project/agent-loop.md`、`project/memory-system.md`

## Resume or Handoff

- 当前目标已完成；若继续推进，下一轮建议做真实 CLI 手工验收或进一步优化 tracer UI
