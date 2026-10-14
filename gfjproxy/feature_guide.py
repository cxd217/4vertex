"""Shared user-facing feature guide for the welcome banner and command help."""

FEATURE_GUIDE = """## 使用方式

把命令写在 JanitorAI 的聊天消息中，可以和正文一起发送，也可以同时写多个命令。
开关命令的 `on` 表示持续开启，`off` 表示关闭，`this` 表示仅本次请求使用。
设置跟随你的 API Key；使用同一个 Key 才能继续读取原设置，Redis 可在重启后保留设置。
看到功能介绍不代表功能已自动开启；发送 `//aboutme` 查看自己的开关状态。

## 预填充与写作预设

原来的成人／NSFW 内容提示和两个写作预设都保留了，是否附加到请求由你的命令设置决定。

- `//prefill on|off|this`：开启或关闭原有预填充提示。
- `//prefill_mode 0|1|2|3`：选择预填充模式，需配合 `//prefill` 开启使用。
  - `0`：旧版经典组合，默认模式。
  - `1`：仅使用 interaction-config 配置提示。
  - `2`：仅使用 starter 提示。
  - `3`：配置提示与 starter 分别组合使用。
- `//preset gigakostyl`：为本次请求附加较短的成人角色扮演写作预设。
- `//preset minipopka`：为本次请求附加较长的叙事与成人角色扮演预设。

`//preset` 只作用于本次请求，需要时再次发送。原有提示保留，生成结果仍取决于模型。

## 对话处理与输出格式

- `//fixturns on|off|this`：末尾补一条 user 消息，处理模型不接受 assistant 结尾的请求。
- `//noass on|off|this`：将聊天历史合并成单条消息。
- `//nobot on|off|this`：移除开头的角色描述，可能影响角色表现。
- `//btrick on|off|this`：发送时使用盲文空格，返回后恢复普通空格。
- `//ooctrick on|off|this`：追加一组 OOC 继续对话提示。
- `//think on|off|this`：附加 think／response 标签格式提示，可能出现残留标签。
- `//think_text keep|remove`：保留或清除上述提示生成的 think 标签文本，需配合 `//think` 使用。

## 搜索与骰子

- `//search on|off|this`：启用 Google Search 工具；搜索发生时附带查询与来源链接，是否支持取决于模型。
- `//dice_char on|off|this`：每次请求为角色提供一个隐藏的 d20 骰子结果。
- `//dice_roll [count]d(faces)[(p|m)(extra)]`：主动掷骰，例如 `//dice_roll 2d6` 或 `//dice_roll d20p3`。

骰子详细规则见 `//help dice`。

## 高级生成参数

先在 JanitorAI 的 Generation Settings 填写对应的有效非零数值，再用下面的命令决定是否传给模型。

- `//advset_temperature on|off|this`：温度参数。
- `//advset_top_k on|off|this`：Top K 参数。
- `//advset_top_p on|off|this`：Top P 参数。
- `//advset_frequency_penalty on|off|this`：频率惩罚。
- `//advset_repetition_penalty on|off|this`：按 presence penalty 传递的重复惩罚。

参数范围和支持情况取决于模型，详情见 `//help advsettings`。
Max Tokens 继续沿用本代理的忽略规则；Context Size 由 JanitorAI 决定发送多少聊天历史。

## 服务功能与访问校验

支持连接 Test、普通聊天、续写、草稿增强、自动摘要，以及 SSE 格式响应。
SSE 沿用先完成生成再封装输出的方式；同一用户有请求并发限制，管理员可以配置请求冷却和带宽告警。
如果模型返回 Token 用量，代理日志会记录输入、输出和相关用量。
`/health` 提供服务状态；`/stats` 提供请求统计，统计数据需要 Redis。

管理员设置访问口令白名单后，JanitorAI 的 API Key 必须是允许的口令。
请求会在调用 Vertex 前检查口令、Gemini 模型、消息结构和参数格式；这不是聊天内容审核。
`401` 表示口令无效，`400` 表示请求格式或参数有误；`503` 可能表示白名单／凭证配置有误，或上游暂时不可用。

## 查看功能和个人设置

- `//aboutme`：查看用户 ID、请求次数、最近使用时间和命令开关。
- `//banner`：再次显示完整功能说明，即使使用 `/quiet/` 也可主动查看。
- `//help commands`：查看这份完整功能说明。
- `//help advsettings`：查看生成参数说明。
- `//help dice`：查看骰子规则。
- `//help vertex`：查看模型和连接说明。

自动横幅在首次成功的普通回复和横幅更新后显示；使用 `/quiet/` 可关闭自动显示。
"""
