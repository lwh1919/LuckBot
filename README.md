# LuckBot

LuckBot 是一个以 `src` 布局组织的个人 AI Agent 项目，CLI 与飞书 gateway 共用同一套运行时、命令系统和插件体系。

## 目录

```text
LuckBot/
├── src/luckbot/
│   ├── entrypoints/          # CLI / gateway 入口
│   ├── application/          # 命令与应用层编排
│   ├── core/                 # runtime / llm / plugin / observability
│   ├── domains/              # session / memory / skills 等领域能力
│   ├── plugins/builtin/      # 内置插件
│   └── adapters/gateway/     # 飞书等 gateway 适配器
├── tests/                    # 测试
├── main.py                   # 本地源码运行入口
├── pyproject.toml            # 打包与脚本入口
└── .luckbot/                 # 本地插件、skills、运行态配置
```

## 入口

- `luckbot`：CLI 入口，对应 `luckbot.entrypoints.cli:main`
- `luckbot-gateway`：gateway 入口，对应 `luckbot.entrypoints.gateway:main`
- `python main.py`：开发态直接运行 CLI

## 当前使用方式

- `luckbot`
  本地交互聊天
- `luckbot ask "..."` / `luckbot cmd /help`
  本地单轮执行
- `luckbot chat --gateway`
  通过正在运行的 gateway 聊天
- `luckbot ask --gateway "..."` / `luckbot cmd --gateway /help`
  通过 gateway 执行单轮请求
- `luckbot gateway serve`
  前台运行 gateway
- `luckbot gateway start|stop|status|logs`
  后台管理本机 gateway 进程

## 设计约束

- 仅保留 `src/luckbot` 作为正式源码包
- CLI 和飞书 gateway 共享 `application + core + domains + plugins`
- transport 适配器放在 `adapters/`
- 领域状态与规则放在 `domains/`
- 插件只负责挂接，不承载跨领域核心模型
