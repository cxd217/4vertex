# 4vertex 升级与 GitHub 更新说明

此包基于你提供的两个压缩包生成：`4vertex-main.zip` → 提供的 `GeminiForJanitors-master.zip`。
本说明用于更新源码和检查现有部署；仓库更新不会自动迁移部署环境变量。

## 已完成的升级

- 使用新版应用工厂、模块化路由、聊天处理和结果解析。
- 保留服务器端 Vertex 服务账号认证，兼容旧环境变量和原用户标识计算方式。
- 新增/保留新版 `//fixturns`、4 种预填充模式、高级参数、搜索结果、Token 用量统计、健康页和统计页。
- 移除 Cerebras、DeepSeek、Nvidia、OpenRouter、Z.AI、通用上游转发、AI Studio API Key 和 Gemini CLI 接入。
- 移除 Keyring 页面/存储、多 Key 轮换、Colab、无用截图和自动推送容器的工作流。
- 保留 Dockerfile、Render 部署、现有 JanitorAI 接口，以及旧的 `gfjproxy.app:app` 入口。
- 密钥不再写入 `vertex_credentials.json`；空 Key、缺少模型/消息和异常后的解锁也已处理。

## 用 GitHub Desktop 更新（适合不熟悉命令行）

1. 解压 `4vertex-updated.zip`，打开其中的 `4vertex-updated` 文件夹。里面应直接看到 `gfjproxy`、`pyproject.toml`、`uv.lock`。
2. 在 [GitHub Desktop](https://desktop.github.com/) 登录你的账号，用 **File → Clone repository** 克隆你自己的 4vertex 仓库。
3. 点击 **Current Branch → New Branch**，创建 `update-vertex` 分支，然后通过 **Repository → Show in Explorer** 打开克隆目录。
4. 将升级包内的全部文件和文件夹复制到克隆目录，替换同名文件。包括 `.github`、`.gitignore`、`.python-version` 等以点开头的文件。不要把外层 `4vertex-updated` 文件夹整体放进去，也不要上传 ZIP 本身。
5. 删除下面列出的旧文件。这些内容已由新版模块替代；不要删除 `.git`、自己的配置或凭证文件。
6. 返回 GitHub Desktop，检查变更列表，提交信息填 `Update 4vertex to latest upstream, Vertex only`，点击 **Commit to update-vertex**，然后 **Publish branch** 或 **Push origin**。
7. 点击 **Create Pull Request**，目标选择你自己仓库的默认分支（通常是 `main`）。等 Python CI 通过后合并。

分支、提交和推送的界面操作可参考 [GitHub Desktop 官方文档](https://docs.github.com/en/desktop/making-changes-in-a-branch/committing-and-reviewing-changes-to-your-project-in-github-desktop)。

相对旧压缩包，应删除的文件：

```text
prefill.txt
think.txt
gfjproxy/static/guide-1.png
gfjproxy/static/guide-2.png
gfjproxy/static/guide-3.png
images/render-1.png
images/render-2.png
images/render-3.png
images/render-4.png
images/render-5.png
images/render-6.png
```

如果你的仓库还有自己加的文件，保留它们并检查是否仍有用途。新的预填充位于 `gfjproxy/prefill.py`。

## 用 Git 补丁更新（自动处理文件新增、修改和删除）

提供的 `4vertex-upgrade.patch` 针对你上传的旧版源码生成，并已验证可应用。
如果 GitHub 仓库与旧压缩包相同，这是最省操作的更新方式。
在 PowerShell 运行，替换用户名和补丁实际路径：

```powershell
git clone https://github.com/你的用户名/4vertex.git
Set-Location 4vertex
git switch -c update-vertex
git apply --check 'C:\你保存补丁的位置\4vertex-upgrade.patch'
git apply 'C:\你保存补丁的位置\4vertex-upgrade.patch'
git diff --stat
git status --short
git add -A
git commit -m 'Update 4vertex to latest upstream, Vertex only'
git push -u origin update-vertex
```

然后在 GitHub 创建 Pull Request，合并到你自己的默认分支。
如果 `git apply --check` 报错，说明仓库与旧压缩包存在差异；不要继续执行后面的命令，改用前面的 Desktop 方式检查并替换。
这套操作使用正常分支和提交，无需 force push。Git 提交/推送见 [GitHub 官方说明](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)。

## GitHub 更新后，检查原部署服务

如果你仍使用 Render，进入原 Web Service，保留原服务和 Redis。
Settings 中改为：

**Build Command**

```bash
pip install uv==0.13.1 && uv sync --locked --no-dev
```

**Start Command**

```bash
uv run --no-sync gunicorn -b 0.0.0.0:$PORT -k gevent -w 1 -t 300 "gfjproxy.app:create_app()"
```

**Health Check Path**：`/health`。Python：3.13。

Environment 中保留 `PROJECT_ID`、完整的 `GOOGLE_CREDENTIALS`、`VERTEX_LOCATION`、`GFJPROXY_XUID_SECRET`、`GFJPROXY_REDIS_URL`。
设置 `GFJPROXY_PROCESS_TIMEOUT=300`；`VERTEX_LOCATION` 未设置时使用 `global`。
不能更换原 `GFJPROXY_XUID_SECRET` 或 Redis，否则无法读到原用户设置。

可选：把 `GFJPROXY_ACCESS_KEYS` 设置成你原来在 JanitorAI 使用的 Key，限制谁能使用你的 Vertex 代理；多人用逗号分隔。
不设置时兼容旧版，接受任何非空 Key；设置了该变量但 Value 为空时会拒绝请求并返回 503。
请求会先校验口令，再检查消息结构、模型和参数，校验失败不会调用 Vertex。
JanitorAI 的 API Key 填白名单中的口令；Google 凭证在服务端。

确认 Render 连接的是合并后的默认分支。开启自动部署时会按服务设置触发；关闭时使用 Manual Deploy 部署最新提交。
已有手动创建的服务不一定会读取仓库里的 `render.yaml`，因此请检查实际命令。
Render 的 Git 分支部署机制见 [官方文档](https://render.com/docs/deploy-flask)。

部署后访问 `/health`，再在 JanitorAI 保存配置并点击 Test。Proxy URL 和原 Key 通常可以继续使用；模型必须是你的 Vertex 项目和区域支持的 Gemini 模型。

## 验证结果和限制

- Python 3.13，`uv sync --locked --all-extras --dev` 安装成功。
- `ruff check` 通过。
- 普通本地环境：157 项通过，4 项 Redis 测试跳过。
- 隔离 Redis 模拟器环境：161 项全部通过；健康检查所用 INFO 字段由测试适配器提供。
- 覆盖 Vertex 全局/区域地址、ADC/JSON 凭证、OAuth 头、系统提示、模型别名、搜索工具、参数、错误、用户标识、访问限制、SSE 格式和锁释放。
- 没有使用真实 Google 凭证发起付费请求，也没有实测你的线上服务或构建 Docker 镜像。真实权限、结算、配额和模型可用性需在部署后通过 Test 确认。

服务账号 JSON 和真实口令只放在部署平台 Environment 中，不上传 GitHub。
