# Skills System Truth

## 文件定位

`project/skills-system.md` 记录 LuckBot skill 系统当前已经实现的真实行为。

本文只覆盖：

- skill 的发现与解析
- skill prompt 注入与运行态状态
- skill activation 预加载
- 项目级 / 用户级目录语义

## 模块职责

当前 skill 系统负责：

- 扫描多个目录中的 `SKILL.md`
- 解析 skill 元信息与参考文档
- 在单次 run 内维护已加载 skill / 已选文档状态
- 在 `before_llm_call` 向 system prompt 注入 skill 信息
- 暴露 `skill_load`、`skill_select_docs`、`skill_get_doc`、`skill_run`
- 支持 inline skill activation directive

## 当前真实行为

### 1. 由 `SkillsPlugin` 与 `SkillActivationPlugin` 共同组成

真实入口位于：

- `src/luckbot/plugins/builtin/skills_plugin/__init__.py`
- `src/luckbot/plugins/builtin/skill_activation_plugin/__init__.py`

`SkillsPlugin` 负责：

- registry 刷新
- skill 工具注册
- `before_run`
- `before_llm_call`

`SkillActivationPlugin` 只负责：

- 读取单次 run 的 skill activation 请求
- 在 `before_run` 中写入 `SkillStateStore`

### 2. skill 发现当前是“项目级优先，用户级兜底”

注册表位于：

- `src/luckbot/domains/skills/registry.py`

当前默认扫描顺序是：

1. `<project>/.luckbot/skills`
2. `~/.luckbot/skills`
3. `LUCKBOT_SKILLS_DIR` 追加的路径

其中项目级相对路径当前按项目根解析，不依赖当前 shell 的 `cwd`。

### 3. 同名 skill 保留首次命中的那个

当前规则是：

- 顺序扫描所有根目录
- 遇到同名 `skill.name` 时，只保留第一次扫描到的那个

因此当前语义是：

- 项目级同名 skill 可以覆盖用户级同名 skill
- `LUCKBOT_SKILLS_DIR` 是追加层，不会反向覆盖更早命中的项目级 skill

### 4. 参考文档自动扫描所有 `.md`

当前会扫描每个 skill 根目录及其子目录下的所有 `.md` 文件，并排除 `SKILL.md` 本身。

当前文档标识是：

- 相对 skill 根目录的路径

例如：

- `docs/guide.md`
- `refs/usage.md`

文档引用必须使用这个相对路径精确匹配；basename 不再作为兼容入口。

### 5. skill 状态当前只在单次 run 内生效

状态存储位于：

- `src/luckbot/domains/skills/state.py`

当前 `loaded` 与 `selected_docs` 都会在每次 run 开始时重置。

因此：

- `skill_load` 不会跨下一轮输入保留
- `skill_select_docs` 也不会跨 run 保留
- skill activation 预加载同样是单次 run 语义

### 6. prompt 注入仍采用 L0 / L1 / L2 三层

`src/luckbot/domains/skills/prompt.py` 当前会在每次模型调用前重建 skill 注入块：

- `L0`
  - 所有已扫描 skill 的概览
- `L1`
  - 已加载 skill 的 `SKILL.md` 正文
- `L2`
  - 已选文档的完整正文

当前注入发生在每轮 ReAct 迭代前，而不是启动时一次性固定。
`SkillsPlugin._before_llm_call()` 只负责调用 domain prompt builder 并把结果接到 system prompt 后。

### 7. 当前用户侧显式 skill 命令是 `/skill list` 与 `/skill show`

当前 CLI / gateway 暴露给用户的 skill 相关 slash command 是：

- `/skill list`
- `/skill show <name>`

这两个命令只做：

- 当前 `SkillsPlugin` 注册的 `skill_registry` service 结果展示
- skill 元信息 / 文档列表展示

它们不直接修改 `SkillStateStore`。
若 `SkillsPlugin` 未加载，命令层会直接提示 `skill_registry` 未注册，不再临时创建独立 registry。

### 8. 当前 skill activation 入口是内联指令

当前有效入口是用户输入中的 inline directive。
入口层解析后会把请求放入：

- `RuntimeContext.requested_skill`

inline directive 由：

- `src/luckbot/domains/skills/directive.py`

负责解析，当前支持：

- `[use skill](name) ...`
- `[use skill name] ...`

`parse_skill_activation_directive()` 当前会：

- 去掉用户输入前缀中的 inline directive
- 生成 `SkillActivationRequest`

当前本地 CLI 已不再暴露 `--skill` / `--skill-doc` 这类参数入口。

### 9. `skill_run` 仍通过隔离工作区执行命令

工作区执行位于：

- `src/luckbot/domains/skills/workspace.py`

当前 `skill_run` 支持：

- 直接执行 `command`
- 只给 `script_content` 时自动写入 `$WORK_DIR/_script.py`
- 未给 `command` 时默认执行 `python3 $WORK_DIR/_script.py`

返回内容包括：

- 退出码
- 耗时
- `stdout`
- `stderr`
- 指定输出文件内容

## 关键入口

- `src/luckbot/plugins/builtin/skills_plugin/__init__.py`
- `src/luckbot/plugins/builtin/skill_activation_plugin/__init__.py`
- `src/luckbot/domains/skills/registry.py`
- `src/luckbot/domains/skills/prompt.py`
- `src/luckbot/domains/skills/state.py`
- `src/luckbot/domains/skills/workspace.py`
- `src/luckbot/domains/skills/directive.py`
- `src/luckbot/core/plugin/hooks.py`
- `src/luckbot/entrypoints/cli.py`
- `src/luckbot/application/commands/executor.py`

## 边界

- skill 负责可复用能力包的发现、选择、文档注入与隔离执行
- skill 不负责长期记忆、session transcript、MCP 联网或 gateway 平台接入
- `.luckbot/skills` 当前是项目工作区私有层，不等于仓库分发层

## 风险

- 同名 skill 只保留首次命中，目录优先级变化会直接改变生效结果
- 追加型 `LUCKBOT_SKILLS_DIR` 不会覆盖更早命中的项目级 skill
- 文档引用必须写完整相对路径，移动文档会影响已有指令
- skill 状态是单次 run 语义，若调用方误以为会跨轮保留，容易造成预期偏差
