# LuckBot Project Truth

## 文件定位

`project/README.md` 是 LuckBot 当前项目真相总入口。

本文件只回答三类问题：

- LuckBot 当前是什么
- LuckBot 当前一级结构是什么
- 其它模块真相文档分别在哪里

## 真相文档地址

### 项目总真相

- `project/README.md`

### 模块真相

- Agent loop：`project/agent-loop.md`
- Runtime：`project/runtime-system.md`
- Plugin：`project/plugin-system.md`
- Observability：`project/observability-system.md`
- Skills：`project/skills-system.md`
- MCP：`project/mcp-system.md`
- Memory：`project/memory-system.md`
- Session：`project/session-system.md`
- Gateway：`project/gateway-system.md`
- 术语表：`project/glossary.md`
- 模块模板：`project/_template.md`

### 相关协议文档

- 工程规则：`workflow/standards.md`
- 任务流程：`workflow/spec-workflow.md`

## 项目目标

LuckBot 当前是一个 CLI 优先、gateway 可复用的 Python Agent 工程。

当前稳定目标是：

- 一个可直接本地使用的交互式 CLI
- 一个可被 CLI 与飞书共同复用的长期运行 gateway
- 一套统一的 runtime / plugin / subagent 基础设施
- 一套可持久化、可检索、可归档的 session 与 memory 系统
- 一套文档优先、spec 驱动的协作方式

## 当前非目标

- 复杂多租户在线平台
- 分布式任务队列与高可用编排
- 完整产品化后台
- 让单轮对话替代项目真相文档

## 当前一级结构

- `main.py`
  - 源码 checkout 下的兼容入口
- `app/`
  - 兼容旧 console script 的桥接层
- `src/luckbot/entrypoints/`
  - CLI / gateway 启动入口
- `src/luckbot/adapters/gateway/`
  - FastAPI gateway、Feishu adapter、dispatcher、service
- `src/luckbot/application/`
  - 命令执行、共享服务、系统提示等应用层
- `src/luckbot/core/`
  - runtime、plugin、llm、observability、config、subagent 等核心基础设施
- `src/luckbot/domains/`
  - session、memory、skills、mcp 等领域模块
- `src/luckbot/plugins/builtin/`
  - 内置插件实现
- `tests/`
  - 回归测试
- `.luckbot/`
  - 项目工作区私有层
  - 当前可包含 `skills/`、`plugins/`、`mcp.json`
  - 默认运行态目录是 `.luckbot/state/`
- `mydocs/`
  - 项目真相、流程协议、spec 与 micro spec

## 当前运行态事实

- 项目级 skills 默认从 `<project>/.luckbot/skills` 扫描
- 项目级 plugins 默认从 `<project>/.luckbot/plugins` 扫描
- MCP 默认配置从 `<project>/.luckbot/mcp.json` 读取
- session / memory / gateway 默认持久化到 `<project>/.luckbot/state`
- 若项目内 state 尚不存在，但旧版 `~/.luckbot` 有运行态数据，系统会把缺失文件复制到项目内 state

## 边界

- `project/` 负责项目知识真相
- `workflow/` 负责流程协议与执行门禁
- `specs/` 与 `micro_specs/` 负责任务级真相
- `.luckbot/*` 代表项目工作区私有层，不默认视为仓库发布物
- 本文件不展开实现细节；具体行为统一下沉到模块真相文档
