# Observability System Truth

## 文件定位

`project/observability-system.md` 记录 LuckBot 当前已经落地的统一观测行为。

本文只写当前代码事实，不写未来设想。

## 模块职责

LuckBot 当前的 observability 模块负责：

- 统一承载 `trace_id`、`run_id`、`langsmith_run_id` 与 gateway 观测身份
- 为 gateway、runtime、memory、session 提供统一 span / metric / structured log 入口
- 用 OpenTelemetry 对接 Grafana 体系
- 用 LangSmith 承载 agent / tool / LLM 深度 trace

## 当前真实行为

### 1. 当前入口是 `core/observability`

当前核心文件位于：

- `src/luckbot/core/observability/context.py`
- `src/luckbot/core/observability/settings.py`
- `src/luckbot/core/observability/telemetry.py`
- `src/luckbot/core/observability/langsmith.py`

其中：

- `ObservabilityContext` 是一次执行链路的标准上下文
- `start_span()` 是 OpenTelemetry span 入口
- `start_langsmith_run()` 是 LangSmith run 入口
- `log_event()` 是结构化日志入口
- `log_exception()` 是结构化异常日志入口

### 2. 当前 observability 只承载观测链路身份

当前 `IncomingTurn.trace_id` 会透传到 `RuntimeContext.trace_id`，再由 `RuntimeContext.to_observability_context()` 派生观测上下文。

当前 `ObservabilityContext` 字段包括：

- `trace_id`
- `run_id`
- `langsmith_run_id`
- `platform`
- `gateway_message_id`
- `gateway_trace_id`
- `chat_id`
- `user_id`

其中：

- `trace_id` 代表整条请求链路
- `run_id` 代表一次 runtime 根执行
- `langsmith_run_id` 当前默认与 `run_id` 对齐

`session_key` / `owner_id` 不属于 `ObservabilityContext` 字段。
runtime 侧日志、metrics 与 LangSmith metadata 需要这些字段时，由 `RuntimeContext` 显式合并。
gateway dispatcher 侧需要这些字段时，由 `IncomingTurn` 显式合并。

### 3. 当前 Grafana 侧的埋点覆盖 gateway 与 runtime 主链

当前 HTTP 入口依赖 FastAPI 自动埋点，不再额外手写 `http.request` span。

代码中当前已显式打点：

- `gateway.enqueue`
- `gateway.run`
- `agent.run`
- `agent.step`
- `llm.call`
- `tool.call`
- `session.resolve`
- `session.flush`
- `memory.owner.activate`
- `memory.before_run`
- `memory.compaction.check`
- `memory.flush_agent`
- `memory.sync`

当前这些 span 通过 `start_span()` 进入 OpenTelemetry 当前上下文。

### 4. 当前日志只记录异常、风险与降级

当前正常流程进展优先由 span、metric 与 LangSmith trace 承载。

当前代码不再用普通 `logger.info()` 记录成功流程，例如：

- 插件初始化成功
- skill 扫描成功
- MCP server 成功加载
- runtime / gateway run start
- workspace 创建与清理

当前稳定日志边界是：

- `warning`
  - 配置缺失或格式不合法
  - 可选能力缺失导致降级
  - 单个外部 server / 文件 / 检索阶段失败但主流程还能继续
- `error` / exception
  - runtime hook 失败
  - gateway run 失败
  - Feishu 响应失败
  - 插件导入、实例化、销毁失败
  - Memory / MCP / LangSmith 等外部或持久化动作失败

在已有 `ObservabilityContext` 的 runtime / gateway 链路中，结构化日志会自动带：

- `trace_id`
- `run_id`
- `langsmith_run_id`
- `platform`
- gateway 相关消息身份

调用方可额外显式传入：

- `session_key`
- `owner_id`
- `error_type`

所以当前日志定位是排障与风险提示，不是正常生命周期流水账。

### 5. 当前 LangSmith 聚焦 agent 深度 trace

当前 LangSmith run 入口位于：

- runtime 根执行 `luckbot.agent.run`
- LLM 调用 `luckbot.llm.call`
- tool 调用 `tool:<name>`
- compaction 摘要 `memory.compaction.merge`

当前 LangSmith 默认关闭，需环境变量启用。

### 6. 当前 memory / session 的辅助观测已从 tracer 插件迁出

当前已删除旧 `trace_aux` service 依赖。

代码事实是：

- skill activation 预加载改走结构化日志
- memory compaction / flush 改走 span event 与 LangSmith / OTel
- session flush / resolve 直接打 span 与 counter

### 7. 当前配置入口以环境变量为准

当前主要配置包括：

- `LUCKBOT_OBSERVABILITY_ENABLED`
- `LUCKBOT_OTEL_EXPORTER_OTLP_ENDPOINT`
- `LUCKBOT_OTEL_EXPORTER_OTLP_HEADERS`
- `LUCKBOT_OTEL_AUTO_INSTRUMENT_FASTAPI`
- `LUCKBOT_SERVICE_NAME`
- `LUCKBOT_SERVICE_VERSION`
- `LUCKBOT_ENV`
- `LUCKBOT_LANGSMITH_ENABLED`
- `LUCKBOT_LANGSMITH_PROJECT`
- `LANGSMITH_API_KEY`

其中：

- `LUCKBOT_LANGSMITH_ENABLED` 是 LuckBot 当前公开的 LangSmith 主开关
- `LANGSMITH_TRACING` 仅作为旧环境变量兼容读取，不建议继续依赖

### 8. 当前本地监控栈样例位于 `monitoring/`

当前仓库内已提供：

- `monitoring/docker-compose.yml`
- `monitoring/otel-collector-config.yaml`
- `monitoring/prometheus.yml`
- `monitoring/grafana/provisioning/...`

这套目录当前用于本地 Grafana / Tempo / Loki / Prometheus / OTel Collector 联调。

## 边界

- observability 负责观测，不负责业务执行
- observability 不决定 session / owner 映射规则
- observability 不作为 session / owner 的业务身份来源
- observability 不替代 transcript / memory 作为事实存储
- LangSmith 当前聚焦 agent 深度 trace，不承担网关与基础设施总览
- 日志不承担正常流程计数与耗时统计职责；这些由 metrics / span 承载

## 风险

- 当前 OTel 依赖是可选导入；依赖未安装时会退化为空实现
- 当前日志仍走标准 logging 输出；若要完整接入 Loki，还需要部署侧采集配置
- 当前 LangSmith 与 Grafana 仍是双系统，需要靠共享 `trace_id` / `run_id` 做跳转关联
