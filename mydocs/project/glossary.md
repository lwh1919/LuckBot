# LuckBot Glossary

## Agent Loop

指 LuckBot 一次 agent run 的生命周期与 ReAct 执行骨架。当前完整生命周期由 `run_runtime()` 承载，LLM/tool 交替执行由 `run_react_loop()` 承载。

## Plugin

LuckBot 的运行时扩展单元。插件可注册工具、hook、service，并由 `PluginManager` 负责依赖排序与初始化。

## Hook

插件在固定生命周期节点插入逻辑的机制。当前关键 hook 包括：
- `before_run`
- `after_run`
- `before_llm_call`
- `after_llm_call`
- `before_tool_call`
- `after_tool_call`

## Tool

由插件暴露给 LLM 调用的能力单元，如 bash、文件读写、grep、memory 搜索、MCP 工具等。

## Skill

可复用的能力包。通常由 `SKILL.md` + 文档/脚本/资源组成，由 Skill 系统在任务中按需加载。

## Spec

当前任务的真相源，描述目标、范围、风险、计划、验证与结论。

## CodeMap

代码导航索引，不是源码复制。用于快速定位入口、链路、依赖、风险点。

## Context Bundle

把零散需求材料提炼成结构化上下文的文档产物，属于一次性输入资产。

## Transcript

会话消息持久化结果，通常由 SessionPlugin 写入 JSONL。

## Reverse Sync

发现需求、实现、验证之间存在偏差时，先同步文档，再继续代码修正。
