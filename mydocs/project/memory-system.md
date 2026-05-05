# Memory System Truth

## 文件定位

`project/memory-system.md` 记录 LuckBot 记忆模块当前已经实现的真实行为。

本文只覆盖：

- 长期记忆目录与索引布局
- `owner_id` 隔离规则
- `memory_search` / `memory_get`
- 压缩、flush、会话归档与 session 的交界

## 模块职责

当前 memory 模块负责：

- 维护长期记忆目录布局
- 把长期记忆 Markdown 同步到统一索引
- 暴露 `memory_search` 与 `memory_get`
- 在上下文超预算时参与 compaction 与 memory flush
- 在 `/session new` 时把最近会话归档成长期记忆 Markdown
- 按 run 级 `owner_id` 切换长期记忆工作区

## 当前真实行为

### 1. 主入口是 `MemoryPlugin`

真实入口位于：

- `src/luckbot/plugins/builtin/memory_plugin/__init__.py`

当前会：

- 构造 `EmbeddingProvider`
- 注册 `memory_sync_now`
- 注册 `before_run`、`before_llm_call`、`after_run`
- 维护 `owner_id -> (MemoryPaths, MemoryIndex, tools...)` 的运行态缓存

当前 `memory_search` / `memory_get` 不是固定绑定到单一 owner，而是随 run 的 `owner_id` 切换。

### 2. 默认长期记忆目录在 `<project>/.luckbot/state/memory/<owner_id>/`

路径入口位于：

- `src/luckbot/domains/memory/paths.py`

当前真实布局：

- `.luckbot/state/memory/<owner_id>/MEMORY.md`
- `.luckbot/state/memory/<owner_id>/memory/**/*.md`
- `.luckbot/state/memory/<owner_id>/index.sqlite`

`state_dir` 默认来自：

- `<project>/.luckbot/state`

若显式设置 `LUCKBOT_STATE_DIR`，则以该目录为准。

### 3. 当前长期记忆严格按 `owner_id` 隔离

当前默认分配是：

- 本地 CLI：`owner_id = local`
- gateway CLI：`owner_id = local`
- 飞书：`owner_id = feishu:user:<open_id>`

所以当前事实是：

- 本地 CLI 与 gateway CLI 共享同一套长期记忆目录
- 同一飞书用户的私聊与群聊共享同一套长期记忆目录
- CLI 与飞书默认不共享长期记忆

### 4. 统一索引只包含长期记忆，不包含 transcript

索引入口位于：

- `src/luckbot/domains/memory/index_db.py`

`MemoryIndex.sync()` 当前只收集：

- `MEMORY.md`
- `memory/**/*.md`
- `extra/*.md`

当前不会把：

- `.luckbot/state/sessions/*.jsonl`

直接并入统一索引。

所以当前 `memory_search` 的检索面只覆盖长期记忆层，不覆盖 raw session transcript。

### 5. `memory_get` 既能读长期记忆，也能显式读旧会话

工具入口位于：

- `src/luckbot/domains/memory/memory_tools.py`

`memory_search` 当前行为：

- 只搜索当前激活 owner 的长期记忆
- 返回 `path`、`source`、`score`、`snippet`

`memory_get` 当前支持：

- `MEMORY.md`
- `memory/*.md`
- `extra/*.md`
- `session:<session_id>`

其中：

- 读 `MEMORY.md` / `memory/*.md` / `extra/*.md` 时遵守当前 owner
- 读 `session:<session_id>` 时直接解析 transcript

### 6. 当前 compaction 属于 memory 侧职责

实现位于：

- `src/luckbot/domains/memory/compaction.py`

当前只有在设置 `LUCKBOT_CONTEXT_TOKEN_BUDGET` 且消息链超预算时才会触发。

压缩结果会：

- 改变当轮传给模型的 `messages`
- 允许摘要消息被 session transcript 白名单持久化
- 由 `SessionPlugin` 检测链变短后改为全量重写 transcript

### 7. 当前 memory flush 只写长期记忆区

实现位于：

- `src/luckbot/domains/memory/flush_agent.py`

当前写入白名单只允许：

- `MEMORY.md`
- `memory/**.md`

不会写：

- `extra/*.md`
- `sessions/*.jsonl`

### 8. 当前显式会话归档入口是 `archive_last_session_to_markdown()`

实现位于：

- `src/luckbot/domains/memory/session_memory.py`

当前 `/session new` 会：

1. 先 flush transcript
2. 再把最近会话提炼成 `memory/YYYY-MM-DD-<slug>.md`
3. 然后同步 memory 索引
4. 最后轮转到新的活动会话

所以当前 `/session new` 同时是：

- 会话边界切换动作
- 长期记忆沉淀触发器

### 9. 当前 project-local state 与 legacy home state 可以平滑衔接

默认 state 根目录切到 `<project>/.luckbot/state` 后，若项目内 state 还不存在而 `~/.luckbot` 有旧数据，当前会复制缺失文件。

所以当前 memory 的真实默认态是：

- 运行在项目内 `.luckbot/state`
- 但迁移期可从旧版 `~/.luckbot` 吸纳历史 memory / session 数据

## 关键入口

- `src/luckbot/plugins/builtin/memory_plugin/__init__.py`
- `src/luckbot/domains/memory/paths.py`
- `src/luckbot/domains/memory/index_db.py`
- `src/luckbot/domains/memory/memory_tools.py`
- `src/luckbot/domains/memory/compaction.py`
- `src/luckbot/domains/memory/flush_agent.py`
- `src/luckbot/domains/memory/session_memory.py`

## 边界

- memory 负责长期记忆索引、检索、压缩、flush 与会话归档
- session 负责活动会话状态与 transcript JSONL
- 当前活跃会话不会自动进入 memory 索引
- `extra/*.md` 当前可读但不可写

## 风险

- `memory_get(session:<session_id>)` 的接口名仍落在 memory 侧，容易让职责边界看起来比实际更宽
- watcher 只绑定首次激活的 owner 目录；其它 owner 的 md 变更主要依赖显式同步或 run 后同步
- `/session new`、compaction、归档链跨 session 与 memory 两侧，改动时必须同时核对两份真相文档
