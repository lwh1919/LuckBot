# LuckBot Task Start Template

## 文件定位

`task-start-template.md` 提供 LuckBot 任务发起时的最小输入模板。

本文件只定义“怎么把任务交给 AI 或协作者”，不定义项目真相，不替代任务 spec，不重复流程规则正文。

执行门禁见 `workflow/standards.md`。

任务分流与阶段顺序见 `workflow/spec-workflow.md`。

## 最小模板

```md
请按 LuckBot 的 mydocs workflow 处理这个任务，不要直接改代码。

Task:
- <一句话描述任务>

Goal:
- <这次要达成什么结果>

Scope:
- <这次允许改哪些模块、目录、文件或链路>

Constraints:
- <不能破坏什么>
- <必须遵守什么>
- <不在本次任务内的内容是什么>

Context:
- <背景、现象、日志、需求来源、讨论结论>

Validation:
- <用什么证明完成，例如测试、日志、手动操作、输出结果>

先给我：
- 任务理解
- 应使用 `micro_specs` 还是 `specs`
- 首版 spec
- research 结论
- plan
- 风险
- 验证方式
```

## 使用规则

- 局部改动、小任务、单点 bug：通常走 `micro-spec`
- 跨模块、长链路、机制调整：通常走 `spec`
- 如果同主题已有未完成 spec，优先更新原文档，不新建
