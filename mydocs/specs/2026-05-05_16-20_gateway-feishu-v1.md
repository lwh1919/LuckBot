# Gateway Feishu V1

## Task

- 为 LuckBot 落地独立 gateway 层，先支持飞书，并保持与 agent/plugin 内核低耦合

## Task Understanding

- 需要的是外层接入架构，不是把飞书做成 agent plugin
- 需要让远程消息能驱动现有 runtime
- 需要修正 session / memory 的 env 单例倾向

## Goal

- 增加可运行的 FastAPI gateway 与飞书 adapter
- 让 runtime 支持按请求传入 `session_key` / `owner_id`
- 补齐对应项目真相文档

## Scope

- `src/gateway/*`
- runtime / hooks / session plugin / memory plugin 的必要微调
- 依赖入口与文档收口

## Constraints

- 不重做 agent loop
- 不把平台接入塞进 plugin system
- 先支持个人使用场景
- 不实现复杂用户隔离、部署编排、多实例调度

## Approval Status

- `Executed`

## Research Findings

- LuckBot 原本只有 CLI 入口
- runtime 已可复用，但 `SessionPlugin` / `MemoryPlugin` 对 env 默认值依赖过强
- `pulse-agent` 的飞书接入形态更接近 gateway / transport layer，而不是 agent plugin

## Plan

- 新增 `gateway/app.py`、`gateway/dispatcher.py`、`gateway/service.py`、`gateway/types.py`
- 新增 `gateway/adapters/feishu.py` 与 `feishu_client.py`
- 扩展 runtime / hook 输入，支持 `owner_id`
- 调整 session / memory 插件优先使用每次 run 的身份参数
- 增加 gateway 与 identity 相关测试
- 回写 project truth 与依赖入口策略

## Risk

- 飞书严格签名校验暂未实现
- 进程内并发保护不适用于多实例
- 远程执行能力开放后，后续仍需单独补“部署动作”策略

## Done Contract

- 存在可启动的 gateway 入口
- 飞书 webhook 能被解析并转成统一输入
- runtime 不再把不同远程会话默认串到同一 session / owner
- 相关真相文档已补齐

## Validation

- `python -m compileall src`
- `pytest -q src/tests/test_agent_runtime.py src/tests/test_gateway.py`
- `pytest -q src/tests/test_plugin_services.py src/tests/test_subagent_runtime.py src/tests/test_trace_and_transcript.py`

## Truth Docs Impact

- 新增 `project/gateway-system.md`
- 更新 `project/README.md` 增加 gateway 索引与一级结构
- runtime / session / memory 的事实变化在各自文档中仍有待进一步细化

## Next Actions

- 增补飞书严格签名校验
- 为 gateway 增加真实端到端联调说明
- 视需要把 runtime / session / memory 真相文档同步到最新身份传递事实

## Change Log

- 增加独立 FastAPI gateway 与飞书 adapter
- runtime / hook / session / memory 增加请求级身份透传
- requirements 入口改为委托 `pyproject.toml`

## Resume or Handoff

- 若继续推进，下一轮应先补 runtime / memory / session 真相文档对 `owner_id` 和 gateway 入口的同步说明
