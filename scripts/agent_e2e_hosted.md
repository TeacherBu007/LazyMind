# GitHub 托管的 macOS / Windows Agent CI

目标工作流：`.github/workflows/agent-e2e-hosted.yml`（Actions 中的 **LazyMind Agent E2E (GitHub-hosted)**）。
这条工作流使用 GitHub 提供的临时 macOS Intel 和 Windows x64 虚拟机。每个 Job 都从当前提交构建并启动 LazyMind，安装三个真实 Agent CLI，创建临时测试账号，通过正常的本地认证接口连接 MCP。无需在触发者电脑上安装 LazyMind 或 Agent，也无需保持个人电脑在线。

## 首次配置

每个仓库的使用者须配置自己的模型服务 Key：

1. 在仓库 **Settings → Secrets and variables → Actions → Secrets** 添加 `LAZYMIND_CI_LLM_API_KEY`、`LAZYMIND_CI_IMAGE_API_KEY`。
2. 在同一页 **Variables** 配置可选的公开路由字段：`LAZYMIND_CI_LLM_BASE_URL`、`LAZYMIND_CI_LLM_MODEL`、`LAZYMIND_CI_LLM_API`、`LAZYMIND_CI_IMAGE_BASE_URL`、`LAZYMIND_CI_IMAGE_MODEL`、`LAZYMIND_CI_IMAGE_API`。
3. 未设置公开字段时使用 `scripts/agent_e2e.providers.example.json` 的默认值：OpenAI Responses / `gpt-6.1-sol`，SiliconFlow / `Kwai-Kolors/Kolors`。Codex 的托管配置需要 Responses 协议；模型须支持工具调用。模型服务需有对应模型的有效权限及余额。

真实 Key 只放在 Secrets 中。不要提交 Key、复制个人账号数据库，或把 Key 填入工作流 YAML。普通非探测的表情包场景需要生图 Key，PPT、论文及只读连接探测只需要 LLM Key。固定测试文本在代码中公开，Agent 与外部模型服务会处理这些测试文本。

## 运行

在 Actions 选择上述工作流并点击 **Run workflow**：

- `agent=all`、`case=all`、`platform=all`：每个平台顺序执行 3 Agent × 3 场景，共 18 个业务检查。
- `probe_only=true`：只验证真实 Agent 的 MCP `workflow.list/get`，不生成业务产物。
- `approve_test_reviews=true`：明确允许测试控制器自动确认本次固定材料的论文大纲和表情包方案；不会审批其他用户任务。不允许自动确认时，审核节点会停止，测试报告会如实记录。
- 单个业务场景失败后会记录失败并继续其他场景；两个平台的矩阵使用 `fail-fast: false`。环境未能启动等公共前置条件失败时，该平台无法开始业务测试，另一平台继续。

每个场景默认最多 30 分钟，每个平台 Job 最多 6 小时。实际运行会消耗 GitHub Actions 配额以及外部模型的用量；没有添加定时触发或付费的大型 Runner。

## 安装与隔离

- Go 1.25.14、Node 22.22.2、pnpm 10.0.0、uv 0.12.10；Python 服务由 LazyMind 源码运行时管理器按其配置安装。
- Python 的 CI 兼容约束见 `scripts/ci-agent-tools/python-constraints.txt`。UMAP / Numba / llvmlite 使用同时提供 Python 3.11 双平台预编译包的版本，避免无人值守环境临时编译 LLVM；不修改本机私有兼容配置。
- 前端构建沿用项目 macOS 安装包工作流的 4 GiB Node 堆内存设置，避免 Vite 构建超出默认堆限制。
- Codex 0.151.0、DeepSeek Harness 0.2.1-rc.2，通过 `scripts/ci-agent-tools/package-lock.json` 安装。WorkBuddy 5.11.4.44121853 来自官方更新服务，安装前核对固定 SHA-256。使用的是 WorkBuddy 自带的 CLI。
- PPT 导出依赖使用工作流自带锁文件安装，并安装、实际启动 Chromium。
- 中文字幕使用来自 Google Fonts 官方仓库固定提交的 Noto Sans SC，核对 SHA-256 并验证汉字字形，附带字体许可；不依赖 Windows 是否预装中文字体。
- 模型 Key 由测试控制器持有；Agent 使用本次启动的本机代理和短期随机令牌。三个 Agent 都使用本次注入的 LazyMind 连接、工作流 ID 和 revision，并在业务前做只读探测；旧连接最多恢复一次。
- Codex 的 CI 临时配置保留 workspace-write 写入限制和审批机制，并显式启用工作流所需的网络访问，使可信生图辅助程序可以连接本次代理及图片服务。
- 账号、数据库、凭据和模型配置放在 VM 的临时目录。上传产物仅包括选定报告、公开构建来源记录和实际 PNG / PPTX / Markdown 交付物；不会上传整个运行目录。
- 报告区分测试通过、业务状态、文件检查及未独立验证的视觉或学术质量，并记录 LazyMind SHA、工作流 SHA、Agent 版本及 LazyMind 工作流 revision。

## 分层排障

`Agent E2E Platform Checks` 可分别选择 `tests`（进程、锁、传输等基础测试）、`runtime`（从源码启动 LazyMind 并读取三个真实工作流）、`agents`（安装三个官方 Agent 并核验版本）、`all`。

基础测试或安装成功不等于业务场景成功。迁移验收以两个系统上实际业务报告和生成文件为准。原有 `agent-e2e.yml` 保留为 macOS self-hosted 的本机排障入口。
