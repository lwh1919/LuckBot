# Session System Truth

## 文件定位

`project/session-system.md` 记录 LuckBot session 模块当前已经实现的真实行为。

本文只覆盖：

- `session_key -> session_id` 的活动会话映射
- transcript JSONL 的恢复、追加、重写与显式导出
- session 与 `owner_id` 的关系

## 模块职责

当前 session 模块负责：

- 维护活动会话的 `session_key`
- 为每个逻辑会话分配稳定的 `session_id`
- 把消息持久化到 `sessions/<session_id>.jsonl`
- 在新进程或新 run 中恢复对应 transcript
- 在压缩后选择追加写还是全量重写
- 为旧会话显式回看提供 transcript 读取入口

## 当前真实行为

### 1. 主入口是 `SessionPlugin`

真实入口位于：

- `src/luckbot/plugins/builtin/session_plugin/__init__.py`

初始化后会注册：

- `before_run`
- `before_llm_call`
- `after_run`
- `session_flush`
- `session_begin_new`

`LUCKBOT_SESSION_PERSIST` 关闭时，插件仍可加载，但不会恢复或持久化 transcript。

### 2. 当前状态模型是 `session_key -> SessionMeta -> session_id`

状态入口位于：

- `src/luckbot/domains/session/state.py`

`SessionMeta` 当前包含：

- `session_id`
- `session_key`
- `updated_at`
- `owner_id`

其中：

- `session_key`
  - 逻辑会话名
  - 是并发保护与 transcript 恢复的身份键
- `session_id`
  - transcript 文件名对应的稳定 UUID
- `owner_id`
  - 该逻辑会话最近一次写回时绑定的长期记忆 owner

### 3. 默认 state 根目录是 `<project>/.luckbot/state`

`resolve_state_dir()` 当前优先级是：

1. `LUCKBOT_STATE_DIR`
2. `<project>/.luckbot/state`

当前默认磁盘布局是：

- `.luckbot/state/sessions/sessions.json`
- `.luckbot/state/sessions/<session_id>.jsonl`

如果项目内 state 目录尚未建立，但检测到旧的 `~/.luckbot` 运行态数据，当前会把缺失文件复制到项目内 state。

### 4. 不同入口的 `session_key` 当前明确分流

当前默认分配是：

- 本地 CLI
  - `local:<session_name>`
- gateway CLI
  - `gateway:cli:<session_name>`
- 飞书私聊
  - `feishu:<open_id>`
- 飞书群聊 @
  - `feishu:group:<chat_id>:<open_id>`

所以当前事实是：

- 本地 CLI transcript 与 gateway CLI transcript 不共享
- 飞书私聊与飞书群聊 transcript 也不共享

### 5. 当前本地 CLI 与 gateway CLI 共享长期记忆 owner，但不共享 session

当前默认 `owner_id` 分配是：

- 本地 CLI：`local`
- gateway CLI：`local`
- 飞书：`feishu:user:<open_id>`

所以当前事实是：

- 本地 CLI 与 gateway CLI 默认共用 `memory/local`
- 但它们仍然各自维护独立 transcript
- 同一飞书用户的私聊与群聊默认共用同一套长期记忆 owner

### 6. transcript 当前仍是 JSONL

相关实现位于：

- `src/luckbot/domains/session/transcript.py`

当前是一行一条 JSON record，核心包含：

- `timestamp`
- `run_id`
- `message`

当前持久化规则：

- `HumanMessage` -> `user`
- `AIMessage` -> `assistant`
- `ToolMessage` -> `tool`
- 普通 `SystemMessage` 默认不落盘
- compaction 摘要白名单 `SystemMessage` 可以落盘

`load_transcript_messages(session_id)` 会按 JSONL 顺序恢复消息；文件不存在或整文件损坏时返回空列表。

### 7. 恢复与写盘由 `before_run` / `after_run` 驱动

`SessionPlugin._before_run()` 当前行为：

- 解析本次 run 的有效 `session_key` / `owner_id`
- 调 `resolve_session()` 取得活动 `session_id`
- 若当前进程已有 `conversation_history`，以内存为准
- 否则从对应 transcript 恢复磁盘消息

`SessionPlugin._after_run()` 当前行为：

- 调 `flush_transcript(...)`
- 再尝试调用 `memory_sync_now`

`flush_transcript(...)` 当前规则：

- 正常情况下只追加新消息
- 若压缩后消息链变短，或 `_force_full_transcript_rewrite` 已置位，则整文件重写

### 8. 当前 session 管理命令是 `/session ...`

当前 CLI / gateway / 飞书共用的 slash command 语义是：

- `/session new`
- `/session save`
- `/session export --format json [--tail N]`

`/session new` 当前流程是：

1. `session_flush`
2. `archive_last_session_to_markdown()`
3. `memory_sync_now`
4. `session_begin_new()`
5. 清空当前内存中的 `conversation_history`

所以当前它不是删除历史，而是：

- 保留旧 transcript
- 可能把旧会话沉淀为长期记忆 Markdown
- 为当前 `session_key` 轮转出新的活动 `session_id`

### 9. 旧会话显式回看仍通过 `session:<session_id>`

当前旧会话读取规范是：

- `session:<session_id>`

读取时底层仍解析：

- `.luckbot/state/sessions/<session_id>.jsonl`

它返回的是整理后的文本视图，不是原始 JSONL 行。

## 关键入口

- `src/luckbot/plugins/builtin/session_plugin/__init__.py`
- `src/luckbot/domains/session/state.py`
- `src/luckbot/domains/session/transcript.py`
- `src/luckbot/domains/session/keys.py`
- `src/luckbot/entrypoints/cli.py`
- `src/luckbot/application/commands/executor.py`

## 边界

- session 负责活动会话状态、transcript 恢复与持久化
- session 维护 `session_key` 与最近一次 `owner_id` 的绑定索引
- session 不负责长期记忆索引、向量检索或 memory flush
- 旧会话显式回看不等于旧会话自动进入长期记忆检索面

## 风险

- `SessionMeta.owner_id` 只记录最近一次写回的 owner；若同一 `session_key` 被不同 owner 复用，后写入值会覆盖前值
- transcript 的追加写与压缩后重写依赖 `_persisted_len` 和 `_force_full_transcript_rewrite`
- `/session new` 同时跨 session 与 memory 两侧，最容易发生文档与实现漂移
