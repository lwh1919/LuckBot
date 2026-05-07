# MCP System Truth

## 文件定位

`project/mcp-system.md` 记录 LuckBot MCP 模块当前已经实现的真实行为。

本文只覆盖：

- MCP 配置读取与刷新
- 工具并入当前 run 的方式
- 项目级 `.luckbot/mcp.json` 的解析语义

## 模块职责

当前 MCP 模块负责：

- 在 `before_run` 读取 MCP 配置
- 按配置连接各 MCP server
- 把远端工具并入当前 run 的工具表
- 在配置未变化时复用缓存结果
- 为 `/mcp list` 提供配置层与工具层的可观测入口

## 当前真实行为

### 1. 主入口是 `MCPPlugin`

真实入口位于：

- `src/luckbot/plugins/builtin/mcp_plugin/__init__.py`

当前只注册：

- `before_run`

销毁时会关闭已持有的 MCP client。

### 2. 默认配置路径是 `<project>/.luckbot/mcp.json`

配置入口位于：

- `src/luckbot/domains/mcp/config.py`

当前默认配置文件仍叫：

- `.luckbot/mcp.json`

但它现在按项目根解析，不依赖当前 shell 的 `cwd`。

如果显式传入绝对路径，则直接使用该绝对路径。

### 3. 当前只识别顶层 `servers`

读取配置后，当前只关心：

- `servers`

若 `servers`：

- 不是 `dict`
- 是空对象

当前都不会向工具表注入任何 MCP 工具。

### 4. 刷新策略受 `LUCKBOT_MCP_REFRESH_POLICY` 控制

当前支持：

- `config`
  - 默认值
  - 配置文件 `mtime` 不变时复用缓存
  - 即使上次成功加载得到 0 个工具，也会复用这次空结果
- `always`
  - 每次 `before_run` 都重建工具

非法值会回退到 `config`。

### 5. 当前支持 `http`、`sse`、`stdio`

归一化逻辑位于：

- `src/luckbot/domains/mcp/loader.py`

当前支持：

- `http`
- `sse`
- `stdio`

不支持的 transport 或缺少必填字段的 server 会被跳过，不会阻断其它 server。

### 6. 当前 stdio server 的 stderr 会被静默处理

stdio MCP server 由第三方 MCP SDK 启动子进程。
SDK 默认会把 server 的 stderr 接到当前进程 stderr；LuckBot 当前在加载 stdio MCP server 前会安装静默 stderr 包装，避免 server 启动提示污染 CLI 对话界面。

因此当前语义是：

- stdio server 的普通 stderr 输出不直接显示在终端
- MCP 配置、连接、拉取工具失败仍由 LuckBot warning / error 记录或命令结果提示
- HTTP / SSE transport 不受这个处理影响

### 7. 当前每个 server 独立加载，单个失败不阻断其它 server

当前流程是：

1. 校验单个 `server_cfg`
2. 归一化 transport 配置
3. 为该 server 单独构造 `MultiServerMCPClient`
4. 拉取工具
5. 用前缀改名后合并到 `tool_map`

因此：

- 某个 server 配置错误
- 某个 server 连接失败
- 某个 server 拉工具时报错

只影响这个 server。
如果某个 server 已创建 client 但拉工具失败，当前会尽力关闭该 client，避免它脱离 registry 生命周期。

### 8. 工具名当前统一改写为 `mcp_<server_name>_<tool_name>`

当前这样做的结果是：

- 不同 server 的同名工具天然隔离
- LLM 实际看到的是 LuckBot 改写后的工具名

若同一 server 自身返回重名工具，后写入者会覆盖前者。

### 9. MCP 工具当前只在 run-time merge 后可用

当前 MCP 插件不会把工具写进常驻工具表。

它只会在 `before_run`：

- 复制 `inp.tools`
- 合并本轮 MCP 工具
- 返回 `BeforeRunResult(tools=merged_tools)`

`MCPPlugin` 同时注册命令查询 service：

- `mcp_config_snapshot`
- `mcp_tool_names`

所以当前 `/mcp list` 通过同一个 `MCPPlugin` / `MCPToolRegistry` 获取配置快照与工具名，
不再由命令层模拟 `before_run`。

### 10. `/mcp list` 现在由共享命令层提供，不在旧 CLI 文件里

当前 `/mcp list` 的实现位于：

- `src/luckbot/application/commands/executor.py`

它会同时展示：

1. `.luckbot/mcp.json` 中声明的 server
2. 当前 `MCPPlugin` 通过 `mcp_tool_names` 暴露的工具名

所以配置存在不代表工具一定成功可用。

### 11. `.luckbot/mcp.json` 仍属于本地运行时输入，不属于项目知识真相本体

当前模块真相只定义：

- LuckBot 如何定位并读取这个文件
- 如何把它转成 MCP 工具

不定义：

- 你应该接哪些 server
- 某个 server 应提供哪些业务工具

## 关键入口

- `src/luckbot/plugins/builtin/mcp_plugin/__init__.py`
- `src/luckbot/domains/mcp/config.py`
- `src/luckbot/domains/mcp/loader.py`
- `src/luckbot/domains/mcp/registry.py`
- `src/luckbot/application/commands/executor.py`

## 边界

- MCP 负责把外部 MCP server 的工具接入当前 run 的工具表
- MCP 不负责定义 server 的业务语义、远端联网质量或服务可用性
- `.luckbot/mcp.json` 是本地运行时配置，不属于项目知识真相正文的一部分

## 风险

- `config` 模式只对配置文件变化敏感，远端服务状态变化但配置未变时不会主动重建
- 工具命名仍依赖 `mcp_<server_name>_<tool_name>` 这一层 LuckBot 前缀
- 若调用方绕过标准 `before_run` 流程直接读取常驻工具表，将拿不到 MCP 工具
