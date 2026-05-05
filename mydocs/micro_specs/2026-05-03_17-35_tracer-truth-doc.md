# Micro Spec

> Historical note (2026-05-06): this spec describes the removed `tracer_plugin` design. LuckBot runtime observability now lives under `src/agent/observability`.

## Task

- 根据 `mydocs/project/_template.md` 新增 tracer 模块真相文档

## Goal

- 产出一份 `mydocs/project/tracer-system.md`
- 只记录 LuckBot tracer 模块当前真实行为、入口、边界与风险

## Scope

- `mydocs/project/_template.md`
- `src/agent/built_in/tracer_plugin/__init__.py`
- `src/agent/built_in/tracer_plugin/collector.py`
- `src/agent/built_in/tracer_plugin/renderer.py`
- 与 tracer 直接耦合的调用点与文档

## Constraints

- 只写当前事实，不写未来设想
- 只新增/回写文档，不改业务代码
- 文档需遵守 `project/_template.md`

## Approval Status

- `Executed and Validated`

## Research Findings

- tracer 当前入口是 `TracerPlugin`
- tracer 当前通过 6 个 hook 被动采集 run 生命周期事件，并在 `after_run` 渲染输出
- tracer 当前还通过 `trace_aux` service 接收主 hook 之外的辅助日志，例如 memory compaction / flush 子流程
- tracer 当前区分 `system prompt` 变化和 `messages` 变化，但不保存完整消息正文

## Plan

- 新增 `mydocs/project/tracer-system.md`
- 结构按模板写成：
  - 文件定位
  - 模块职责
  - 当前真实行为
  - 关键入口
  - 边界
  - 风险

## Validation

- 对照模板检查结构是否完整
- 对照 tracer 代码检查关键事实是否可落到文件或函数
- 已新增 `mydocs/project/tracer-system.md`
- 已把 `project/README.md` 真相目录挂上 tracer 文档入口
