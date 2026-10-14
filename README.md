# 4vertex · Vertex AI 专用版

这是基于提供的 `GeminiForJanitors-master.zip` 升级的 4vertex。
保留 JanitorAI 代理和新版聊天功能，使用 Google Cloud Vertex AI 服务账号认证。
原项目：[vu5eruz/GeminiForJanitors](https://github.com/vu5eruz/GeminiForJanitors)。沿用原 MIT 许可证。

## 本版保留的功能

- JanitorAI 连接测试、聊天、续写、草稿增强、自动摘要。
- 原来的 `/`、`/chat/completions`、`/quiet/` 接口，以及 SSE 响应格式。
- 新版 `//fixturns`、`//prefill_mode`、高级生成参数、搜索工具、预设和现有聊天命令。
- `/health`、`/stats` 页面；Redis 用户设置和统计；冷却时间与带宽监测。
- 兼容原来的 `PROJECT_ID`、`GOOGLE_CREDENTIALS`、`VERTEX_LOCATION`。

已移除其他模型供应商、AI Studio API Key 接入、Gemini CLI OAuth/Keyring、多 Key 轮换、Colab、其他平台部署截图和自动发布容器的工作流。
Dockerfile 仍保留，方便自行部署。

## 从旧版迁移

1. 将新源码更新到你现有的 GitHub 仓库，参见 [GitHub 更新说明](GITHUB_UPDATE.md)。
2. 保留原来的 `PROJECT_ID`、`GOOGLE_CREDENTIALS`、`VERTEX_LOCATION`、`GFJPROXY_XUID_SECRET` 和 `GFJPROXY_REDIS_URL`。
3. 更新部署命令，尤其是 300 秒的进程超时；新版 Vertex 请求超时默认是 290 秒。
4. JanitorAI 的 Proxy URL、Key 和可用 Gemini 模型可以继续使用。

同一个 JanitorAI Key、同一个 `GFJPROXY_XUID_SECRET` 和同一个 Redis 才能保留原用户设置。
旧配置如果包含逗号分隔的多个 Key，仅使用第一个识别用户，不再轮换。
现在只允许每次请求指定一个 Gemini 模型。

## Render 部署

已有服务直接修改 Settings 和 Environment 即可。更新仓库里的 `render.yaml` 不一定会改变手动创建的服务设置。
新服务可以使用本仓库的 Blueprint；其中保留了旧模板的服务名称。
Render 支持 Flask/Gunicorn，具体设置位置见 [Render 官方文档](https://render.com/docs/deploy-flask)。

Build Command：

```bash
pip install uv==0.13.1 && uv sync --locked --no-dev
```

Start Command：

```bash
uv run --no-sync gunicorn -b 0.0.0.0:$PORT -k gevent -w 1 -t 300 "gfjproxy.app:create_app()"
```

Health Check Path：`/health`。Python：3.13（仓库已提供 `.python-version`）。
旧入口 `gfjproxy.app:app` 和 `gfjproxy.app:application` 也可用；建议采用上面的启动命令。
`requirements.txt` 是从锁文件导出的运行依赖，也可用于 `pip install -r requirements.txt`。

| 环境变量 | 填写方式 |
| --- | --- |
| `PROJECT_ID` | 你的 Google Cloud 项目 ID；可继续用旧值 |
| `GOOGLE_CREDENTIALS` | 完整的服务账号 JSON；放在部署平台环境变量中 |
| `VERTEX_LOCATION` | 默认 `global`；也可填写模型支持的区域 |
| `GFJPROXY_XUID_SECRET` | 保留旧值；新部署设置为随机长字符串 |
| `GFJPROXY_REDIS_URL` | 可选，沿用原 Redis URL；持久化设置和统计需要它 |
| `GFJPROXY_ACCESS_KEYS` | 可选，允许访问此代理的口令，多个用逗号分隔 |
| `GFJPROXY_EXTERNAL_URL` | 可选，你的公开代理 URL，供帮助信息使用 |
| `GFJPROXY_PROCESS_TIMEOUT` | 建议 `300`，应与 Gunicorn 的 `-t` 相符 |
| `GFJPROXY_COOLDOWN` / `GFJPROXY_ADMIN` | 可沿用旧值 |

`GFJPROXY_ACCESS_KEYS` 未设置时保持旧版行为：任何非空 Bearer Key 都可作为用户标识。
设置后，JanitorAI 的 Key 必须匹配允许列表中的一个口令。可以直接加入你原来使用的 Key，保持用户设置。
这类口令只是代理访问口令，不是 Google API Key，也不是服务账号 JSON。

无 Redis 时仍能运行，但应使用单个 worker（`-w 1`）；重启会清除设置，统计页不会累计数据。
不要在生产环境设置 `GFJPROXY_DEVELOPMENT`。

## JanitorAI 设置

在 API Settings 中选择 Proxy，添加并启用配置：

- **Model Name**：例如 `gemini-2.5-flash`；也接受 `google/gemini-2.5-flash`、`vertex/gemini-2.5-flash`。
- **Proxy URL**：你部署后的服务网址；需要隐藏横幅时使用 `/quiet/`。
- **API Key**：保持原来的固定 Key；若启用访问限制，使用管理员允许的口令。

保存后点击 Test。Google 的认证在服务器端完成，JanitorAI Key 不会传给 Google。
服务账号需要目标项目的 Vertex AI 使用权限，项目需要启用 Vertex AI API 和有效结算。
不同模型的区域可用性见 [Google 官方端点文档](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations)。

## 本地开发（Windows PowerShell）

先安装 uv 和 Python 3.13，然后在项目根目录运行：

```powershell
uv sync --locked --all-extras --dev
$env:GFJPROXY_DEVELOPMENT = '1'
$env:PROJECT_ID = '你的项目ID'
$env:GOOGLE_APPLICATION_CREDENTIALS = 'C:\安全目录\服务账号.json'
$env:VERTEX_LOCATION = 'global'
uv run flask --app 'gfjproxy.app:create_app' run --host 127.0.0.1 --port 5000
```

`.env.example` 只是参考，程序不会自动加载 `.env`。
除了 `GOOGLE_CREDENTIALS`，还支持 `GOOGLE_APPLICATION_CREDENTIALS` 和 Google Application Default Credentials。
服务账号环境变量在内存中读取，访问令牌由 google-auth 在需要时刷新。
相关机制见 [google-auth 官方说明](https://github.com/googleapis/google-auth-library-python/blob/main/docs/user-guide.rst)。

## 开发检查

```powershell
$env:GFJPROXY_DEVELOPMENT = '1'
uv run ruff check
uv run pytest
```

没有 Redis 时，与 Redis 有关的 4 项测试会跳过；GitHub CI 会启动 Redis 来运行它们。
测试通过模拟 Vertex 响应验证行为，不需要真实 Google 密钥。
本版保持上游的 SSE 封装方式：先完成生成，再封装输出，并非逐 token 从 Google 转发。
Max Tokens 继续按提供的上游版本忽略；其他高级参数由对应 `//advset_*` 命令启用。

## 常见问题

- **503 / credentials missing**：检查服务端 `GOOGLE_CREDENTIALS` 是否为完整 JSON，或是否配置 ADC。
- **502 / authentication failed**：检查服务账号密钥是否有效。
- **403 / PERMISSION_DENIED**：检查目标项目权限、Vertex AI API、结算状态。
- **404 / NOT_FOUND**：检查模型名称及该模型支持的区域。
- **429 / RESOURCE_EXHAUSTED**：Vertex 仍可能遇到配额或容量限制；依据具体错误调整频率。
- **ending with a model turn**：尝试 `//fixturns on`。

服务账号 JSON、真实口令和 `.env` 不应上传到 GitHub；仓库已补充相应忽略规则。
安全设置遵循 Vertex API 的可配置项，实际模型仍可能返回过滤或空响应。
