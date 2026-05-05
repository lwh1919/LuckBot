# Spec

> Historical note (2026-05-06): this spec may mention retired tracer-era services such as `trace_aux`. Current observability no longer depends on them.

## Task

- 收紧 LuckBot 插件系统中的 service 语义，修正不合理的 service 暴露与生命周期问题

## Task Understanding

- 当前插件系统通过 `PluginContext` 暴露 `service`
- 现有问题主要集中在三类：
  - service 重名时仅 warning 覆盖，运行时语义不稳定
  - plugin manager 销毁后 service 仍可被读取，存在失效对象泄漏
  - 部分 service 暴露的是内部实现对象，但当前生产代码并未消费

## Goal

- 收紧 service 的注册语义与生命周期语义
- 移除当前不必要的 service 暴露
- 保持当前实际使用链路可用

## Scope

- `src/agent/plugin/base.py`
- `src/agent/plugin/manager.py`
- `src/agent/built_in/memory_plugin/__init__.py`
- `src/agent/built_in/skills_plugin/__init__.py`
- 相关测试
- 必要的 `mydocs/project/plugin-system.md` 回写

## Constraints

- 不改 `tool` 和 `hook` 的现有对外行为
- 不破坏当前 CLI、memory、session、remote skill 的已使用协作链路
- 优先保留能力型 service，收紧内部对象型 service

## Approval Status

- `Executed`

## Research Findings

- `register_service()` 当前重名只 warning 后覆盖
- `PluginManager.destroy()` 当前不清理 `_ctx` 内部注册表
- 当前生产代码实际消费的 service 是：
  - `session_flush`
  - `session_begin_new`
  - `memory_sync_now`
  - `skill_registry`
  - `skill_state_store`
  - `trace_aux`
- 当前未被生产代码消费的 service 是：
  - `memory_index`
  - `memory_paths`
  - `skill_runtime`

## Plan

- 将 service 重名注册改为直接失败
- 在 manager 销毁后重置 `PluginContext`，避免 service 泄漏
- 删除 `memory_index`、`memory_paths`、`skill_runtime` 三个未消费 service 的注册
- 补测试覆盖 service 重名与 destroy 后清理行为
- 回写 plugin system 真相文档

## Risk

- 现有测试如果依赖未使用 service 的存在，需要同步更新
- destroy 后重置 context 会改变“销毁后还能读旧注册表”的隐式行为

## Done Contract

- service 重名注册不再 silent overwrite
- destroy 后无法再拿到旧 service
- 当前已使用 service 链路仍成立
- 未使用的内部对象型 service 不再暴露

## Validation

- 检查当前代码与 project 真相描述一致
- `pytest -q src/tests/test_plugin_services.py`
- 当前专项测试覆盖：
  - service 重名注册拒绝
  - manager destroy 后清空运行时注册表
  - remote skill 仍通过 `skill_registry` / `skill_state_store` 正常协作
  - session 在 `after_run` 后仍调用 `memory_sync_now`

## Next Actions

- 无

## Change Log

- `register_service()` 改为重名直接失败，不再 silent overwrite
- `PluginManager.destroy()` 结束时重置 `PluginContext`，清空运行时注册表
- 移除 `memory_index`、`memory_paths`、`skill_runtime` 三个未消费 service 的注册
- 新增 `src/tests/test_plugin_services.py`，补回当前 service hardening 的自动化验证

## Resume or Handoff

- 当前任务已完成。
- 若继续收紧插件系统，可下一轮处理工具重名与插件名冲突问题。
