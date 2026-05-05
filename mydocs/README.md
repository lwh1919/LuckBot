# LuckBot Docs Index

## 文件定位

`mydocs/README.md` 是 `mydocs/` 的总入口。

本文件只回答三件事：

- `mydocs/` 下各目录的职责
- 文档体系分成哪几层
- 开始一个任务时的全局默认阅读顺序

## 文档分层

- `project/`
  - 项目知识真相层
- `workflow/`
  - 任务推进协议层
- `micro_specs/`
  - 轻量任务真相层
- `specs/`
  - 长链路任务真相层

## 目录说明

### `project/`

项目知识真相。

- 项目总真相：`project/README.md`
- 模块真相：`project/*.md`
- 真相模板：`project/_template.md`

### `workflow/`

任务推进协议。

- 工程规则：`workflow/standards.md`
- 任务流程：`workflow/spec-workflow.md`
- 任务发起模板：`workflow/task-start-template.md`

### `micro_specs/`

轻量任务 spec。

- 小任务
- 单点 bug
- 局部修改
- 默认任务载体

### `specs/`

扩展任务 spec。

- 长链路任务
- 跨模块任务
- 需要更完整研究、计划、验证记录的任务

## 默认阅读顺序

开始一个任务时，默认按以下顺序读取：

1. `project/README.md`
2. 当前任务相关模块真相文档
3. `workflow/standards.md`
4. `workflow/spec-workflow.md`
5. 当前任务 spec
6. 必要时再读代码

## 边界

- `project/` 不写流程细节，只写项目知识真相
- `workflow/` 不写项目模块实现，只写流程、门禁与回写规则
- `micro_specs/` 与 `specs/` 只描述单次任务

## 跳转入口

- 项目总真相：`project/README.md`
- 流程硬规则：`workflow/standards.md`
- 流程阶段流：`workflow/spec-workflow.md`
- 真相模板：`project/_template.md`
- micro-spec 模板：`micro_specs/_template.md`
- spec 模板：`specs/_template.md`
