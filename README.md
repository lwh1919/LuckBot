# LuckBot

LuckBot 是一个个人 AI Agent 工程，当前以本地 CLI 为主，同时提供可复用的 gateway，让 CLI remote 与飞书入口共用同一套 runtime、命令系统、插件系统、session 和 memory。

它当前能做的事情：

- 本地终端交互聊天与单轮 `ask`
- 通过 gateway 连接本机或远程 bot
- 接入飞书文本消息
- 持久化 session transcript
- 将 session 沉淀为长期记忆 Markdown，并提供 `memory_search` / `memory_get`
- 通过 skill 系统注入可复用能力文档
- 通过 MCP 把外部 server 的工具接入本轮 agent
- 通过插件统一装配 tools、hooks、services
- 通过 OpenTelemetry / LangSmith 提供基础观测能力

项目仍处在个人工程与能力验证阶段，不是成熟的多用户生产平台。

## 快速启动

### 1. 安装

```bash
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少填写：

```env
LUCKBOT_MODEL_BASE_URL=https://api.openai.com/v1
LUCKBOT_MODEL_API_KEY=your-api-key
LUCKBOT_MODEL_NAME=gpt-4o-mini
```

记忆检索需要额外配置 embedding：

```env
DASHSCOPE_API_KEY=your-dashscope-key
LUCKBOT_MEMORY_EMBEDDING_MODEL=text-embedding-v4
LUCKBOT_MEMORY_EMBEDDING_DIM=1024
```

### 3. 本地使用

```bash
luckbot
```

单轮执行：

```bash
luckbot ask "帮我总结一下这个项目的结构"
luckbot ask "/help"
```

清空当前 owner 的长期记忆：

```bash
luckbot memory clear
```

### 4. Gateway 使用

启动或复用本机 gateway，并进入通过 gateway 的终端聊天：

```bash
luckbot gateway
```

连接远程 gateway：

```bash
luckbot gateway http://127.0.0.1:8000
```

管理本机 gateway：

```bash
luckbot gateway start
luckbot gateway status
luckbot gateway logs
luckbot gateway stop
```

CLI remote 的简单鉴权使用：

```env
LUCKBOT_GATEWAY_SERVER_KEY=server-secret
LUCKBOT_GATEWAY_CLIENT_KEY=server-secret
```

## 文件树

```text
LuckBot/
├── src/luckbot/
│   ├── entrypoints/              # CLI 入口
│   ├── application/              # turn runner、命令层、gateway client/control
│   ├── adapters/
│   │   └── gateway/              # FastAPI gateway、dispatcher、Feishu adapter
│   ├── core/
│   │   ├── runtime/              # 标准 agent runtime 与 ReAct loop
│   │   ├── plugin/               # 插件契约、hook、manager
│   │   ├── llm/                  # LLM client 构造
│   │   ├── observability/        # OTel、LangSmith、结构化日志
│   │   └── config/               # 环境变量与项目路径
│   ├── domains/
│   │   ├── session/              # session_key、transcript、状态目录
│   │   ├── memory/               # 长期记忆、索引、检索、压缩、归档
│   │   ├── skills/               # SKILL.md 扫描、prompt 注入、skill_run
│   │   └── mcp/                  # MCP 配置读取与工具加载
│   └── plugins/
│       └── builtin/              # 内置 tools / session / memory / skills / mcp 插件
├── mydocs/                       # 项目真相、流程协议、spec
├── monitoring/                   # 本地 OTel / Grafana / Tempo / Prometheus 样例
├── tests/                        # 回归测试
├── .luckbot/                     # 本地私有 workspace：skills、plugins、mcp.json、state
├── .env.example                  # 环境变量示例
└── pyproject.toml                # 包配置与 luckbot 命令入口
```

## 模块说明

### Runtime：统一执行骨架

LuckBot 的核心不是某个入口，而是一套统一的 agent run 生命周期。

- `RuntimeContext` 承载本轮消息、身份、工具表、prompt 与观测信息
- `run_runtime()` 负责一次完整 run 的生命周期
- `run_react_loop()` 负责 LLM / tool 交替执行
- 每轮 LLM 调用前后、每次 tool 调用前后，都可由插件 hook 介入

不同入口最终都会进入同一条 runtime 链路，因此 CLI、Gateway、飞书不会各自维护一套 agent 内核。

### Plugin：能力装配机制

插件系统负责把运行时能力统一装配进 agent，而不是让 runtime 直接依赖具体业务模块。

插件可以注册三类能力：

- `tools`：暴露给模型调用
- `hooks`：接入 `before_run`、`before_llm_call`、`before_tool_call` 等生命周期
- `services`：供命令层或插件之间显式调用

插件通过依赖关系做拓扑排序，通过 hook priority 控制同类 hook 的执行顺序。内置能力如 memory、session、skills、mcp、tools 都通过插件接入；项目级私有插件默认从 `.luckbot/plugins` 扫描。

### Memory：长期记忆、混合检索与压缩

记忆系统负责把会话沉淀成长期 Markdown，并为 agent 提供可检索的长期上下文。

当前长期记忆按 `owner_id` 隔离，默认位于 `.luckbot/state/memory/<owner_id>/`：

- `MEMORY.md`
- `memory/**/*.md`
- `index.sqlite`

检索链路是混合检索：

- Markdown 被切分为 chunk 后写入 SQLite
- `chunks_fts` 提供 FTS5 / BM25 全文检索
- embedding 写入缓存与 chunk 表，可选 `sqlite-vec` 做 KNN
- `memory_search` 将全文分数与向量相似度加权融合
- 可选 rerank / MMR 做结果重排与去冗余

`memory_search` 当前只检索长期记忆，不直接检索 raw session transcript。`/session new` 会先 flush transcript，再把最近会话归档为长期记忆 Markdown 并同步索引。

上下文超预算时，memory compaction 会触发摘要与最近消息保留；保留最近消息时会维护 tool call 配对，避免把孤立 `ToolMessage` 写回 session。

### Skills：渐进式三级 CQRS

Skill 系统用于沉淀可复用流程能力，不把所有能力文档一次性塞进 prompt。

当前采用 L0 / L1 / L2 三层渐进式注入：

- `L0`：所有已扫描 skill 的概览，提示模型何时加载技能
- `L1`：通过 `skill_load` 加载某个 skill 的 `SKILL.md` 正文
- `L2`：通过 `skill_select_docs` 选择参考文档并注入全文
- `skill_get_doc` 可即时读取单个文档，不进入持续注入状态
- `skill_run` 可在隔离工作区执行技能脚本或命令

这里的 CQRS 边界是：

- tool 调用侧负责写入本轮 skill state
- `before_llm_call` hook 负责读取 state，并重建 L0 / L1 / L2 prompt 注入块
- skill state 只在单次 run 内生效，下一轮重新开始

skill 默认扫描 `.luckbot/skills`、`~/.luckbot/skills` 与 `LUCKBOT_SKILLS_DIR` 追加路径；项目级同名 skill 优先于用户级同名 skill。

### Session：短期 transcript 与长期记忆边界

Session 负责短期对话状态，不直接承担长期检索。

当前 session 以 `session_key -> session_id -> sessions/<session_id>.jsonl` 持久化。不同入口默认使用不同 `session_key`，本地 CLI、gateway CLI、飞书私聊、飞书群聊 transcript 不直接混用。

transcript 读写会规范化 tool call 配对：

- 孤立 `ToolMessage` 会被过滤
- 不完整或重复的 tool call 片段会整组丢弃
- compaction 摘要是允许落盘的白名单 `SystemMessage`

所以 session 是“当前对话上下文”，memory 是“可检索长期知识”，两者通过 `/session new`、compaction 与 memory flush 串联。

### MCP：外部工具接入

MCP 负责把外部 server 的工具并入本轮 agent 工具表。

当前默认读取 `<project>/.luckbot/mcp.json`，支持 `http`、`sse`、`stdio` transport。工具名会统一改写为 `mcp_<server_name>_<tool_name>`，避免不同 server 的同名工具互相覆盖。

MCP 工具只在 run-time merge 后进入当前工具表，不写入常驻工具表；stdio server 的普通 stderr 会被静默处理，避免污染 CLI 对话界面。

### Observability：运行链路观测

观测系统负责把一次 agent run 和内部 LLM / tool / memory / session 过程串起来：

- OpenTelemetry 负责 span 与 metric，可接入本地 Grafana / Tempo / Prometheus 样例栈
- LangSmith 负责 agent、LLM、tool 与 compaction 摘要的深度 trace
- 结构化日志主要记录 warning、error、降级和异常，不承担正常流程流水账

核心关联字段是 `trace_id` 与 `run_id`；runtime 会把 `session_key`、`owner_id` 等业务身份显式合并进 metrics、日志与 LangSmith metadata。

### Docs：项目真相与执行门禁

`mydocs/` 是 LuckBot 的文档协议层：

- `mydocs/project/` 记录稳定项目真相
- `mydocs/workflow/` 记录流程规则与执行门禁
- `mydocs/specs/`、`mydocs/micro_specs/` 记录任务级目标、计划、验证与回写

LuckBot 当前开发方式是 **spec workflow + vibe coding**：

- spec workflow 用来固定任务目标、执行门禁、验证合同与事实回写
- vibe coding 用来保持高频探索、快速试错和人机协作节奏
- 两者结合的原则是：可以快，但不能跳过 spec、计划、批准、验证和真相文档回写

代码、README 与对话出现冲突时，优先回到 `mydocs/project/` 和当前任务 spec 校准事实。

## TODO
- 门禁：缺少human in the loop（ctx超时式与checkpoint式）及工具调用门禁
- 记忆系统精细化：更好的 CJK 检索、更稳定的 chunk 策略、更明确的事实/偏好/任务分类。
- 做梦机制：定期从历史会话和长期记忆中提炼经验，生成可复用的 skill，如当前场景问几点了，ai没有直接的查看时间的工具，但是可以利用bash查看时间。
- 短期记忆收缩：处理那些没有进入长期记忆、但仍占用 transcript 的旧短期上下文。
- Gateway 强化：当前 gateway 只是简单跑通，缺少真正的多用户隔离、权限模型、配额和部署级并发保护。
- 测试补齐：跨 CLI / gateway / memory / plugin / observability 的回归覆盖还不够。
- tool的执行结果压缩+语义限定，例如skill的workspace
- 沙箱能力：当前工具沙箱还不够全面，隔离、权限、资源限制和产物管理都需要加强。
- 提示词调试：系统提示词、memory flush prompt、skill prompt 注入策略还没有经过系统评测。
- 生产任务实践：自动 U 学院签到、AI 写作业等实际场景还没有完整产品化验证。
- Subagent tool 化：当前独立 runtime run 还没有整理成一套清晰可调用的 subagent tool 能力。
- Team 功能：多 agent 分工、协作和交接机制还没有实践。
