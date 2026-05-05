# LuckBot 工作指南

- 使用中文交流。
- 永远不要执行 `git clean`。
- 文档优先于对话记忆。
- `No Spec, No Code`
- `No Approval, No Execute`
- `Spec is Truth`

## 默认协议

LuckBot 仓库默认使用 `mydocs` 协议。

- 项目真相：`mydocs/project/`
- 流程硬规则：`mydocs/workflow/standards.md`
- 流程阶段流：`mydocs/workflow/spec-workflow.md`

## 默认阅读顺序

开始任务时，默认按以下顺序读取：

1. `mydocs/README.md`
2. `mydocs/project/README.md`
3. 当前任务相关的 `mydocs/project/*.md`
4. `mydocs/workflow/standards.md`
5. `mydocs/workflow/spec-workflow.md`
6. 当前任务 spec
7. 必要时再进入代码

如果文档与代码冲突，先核对事实，再决定修文档还是修代码；不要直接忽略文档。

## 执行规则

- 先复述任务理解，再建立或更新任务 spec
- 先给计划或 checkpoint，再等待批准
- 只有收到 `Plan Approved` 或 `批准执行`，才允许进入代码修改
- `Implement the plan`、`可以继续`、`搞吧` 这类表达默认不等于批准词
- 执行后先验证，再宣布完成
- 发现偏差时，先回写 spec，再决定下一轮动作
- 稳定模块事实变化后，回写 `mydocs/project/*.md`
- 协议变化后，回写 `mydocs/workflow/*.md`

## 命令边界

- 除非我明确要求，否则不要主动运行编译、打包、测试、部署、迁移或其他高开销命令
- 除非我明确要求，否则不要主动安装依赖、升级依赖、删除依赖或修改锁文件
- 对可能造成不可逆后果、批量删除、覆盖、重置、迁移或外部写操作的命令，先等待我确认

## Git 边界

- 默认尊重 `.gitignore` 与所有已忽略路径
- 不要主动使用 `git add -f` 或 `git add --force`
- 被 `.gitignore` 忽略的内容默认不提交，除非我明确点名路径
- `.luckbot/*` 默认视为项目工作区本地私有资产，不作为仓库发布物
