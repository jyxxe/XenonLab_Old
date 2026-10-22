# XeonoLab Agent 提示词目录

本目录是多 Harness Agent 的提示词目录，替代仓库根旧的单一通用示例 `docs/agent-workflow.md`（已过期，仅作历史参考）。每个文件对应一类 Agent 的职责、权限边界与工作方式，可直接复制给对应 Harness，也可由 Xenon Panel 的“提示词”页面读取展示。

- 目录索引：`manifest.json`（面板与构建脚本读取，5 个 id 与文件名固定）
- 语言：中文 Markdown；单文件聚焦可执行规则，不写营销文案
- 网络：内网 Agent 与公网 Agent 使用不同提示词，**不要把内网版发给公网 Agent**

## 提示词一览

| id | 文件 | 适用 Agent | 网络 | 核心职责 |
|---|---|---|---|---|
| `codex` | `codex.md` | Codex 协调器 | LAN | 工作租约、验收、远端写入、PR；release → approval → 排他租约 → 固定 executor 发布与证据 |
| `dsh` | `dsh.md` | DeepSeek Harness 维护 / 发布 Agent（`devagent-maintainer`） | LAN | `devctl` 评审、合并门禁、部署、终态核实；publication 证据写入 `maintainer.sqlite` 与 `admin-runlog.md` |
| `opencode` | `opencode.md` | OpenCode 开发 Harness | LAN | 本地实现与回归；写入请求 / 交付报告交协调器；不自动合并或发布 |
| `general-agent-lan` | `general-agent-lan.md` | 通用 Bug 搜寻 / Issue 处理 Agent（内网） | LAN | 直连 `192.168.0.x` 与 LAN CA；Work 认领、合成 fixture 复现、回归测试、附证据 PR |
| `general-agent-wan` | `general-agent-wan.md` | 通用 Bug 搜寻 / Issue 处理 Agent（外网） | WAN | 只用公网域名与公共 CA；经 Forgejo REST 提交 Issue/分支/PR；请求维护者合并与发布 |

`manifest.json` 字段说明：`id`、`title`、`audience`、`network`（`lan` / `wan`）、`file`、`summary`；`updated` 为最近一次内容修订日期。**id 与文件名是面板接口约定，不得改名。**

## 如何读取

### Xenon Panel（推荐）

内网桌面 → **System → 提示词**。面板在构建时打包本目录的 `manifest.json` 与 5 份 Markdown，列表显示标题、适用对象、内网/外网标识与摘要，选中后可预览、复制、下载。

### 直接通过 LAN 访问

内网边缘按文件名直接提供本目录内容（无需登录面板即可阅读）：

- LAN：`https://192.168.0.101:9443/agent-prompts/<file>.md`
- 公网 / Tunnel：`https://xenon.jyxxe.com/agent-prompts/<file>.md`

示例：`https://192.168.0.101:9443/agent-prompts/general-agent-lan.md`、`https://xenon.jyxxe.com/agent-prompts/general-agent-wan.md`。公网只应获取**公网版**提示词；内网专用提示词包含内网地址，不要复制到公网可写位置。

### 仓库内阅读

在 Forgejo 的 `feature/platform-v1` 分支浏览 `docs/agent-prompts/`：LAN `https://192.168.0.101:9445/dev/`，公网 `https://xenon.jyxxe.com/dev/`。

## 凭据规则（硬性）

- **提示词里永远不出现凭据**：没有 token、密码、client secret、私钥、cookie、连接串或长随机串；只有路径约定。
- 每个 Agent 的身份与凭据存放在私有 0600 文件里，只读取分配给自己的文件：
  - 仓库/运行目录约定：`runtime/agent-profiles/<harness>/Agent.md`（目录 0700、文件 0600，Git 忽略）；
  - 个人工作站约定：`~/.config/devagent/<harness>/` 下的 0600 文件。
- 发布、PVE、部署凭据由 root 拥有的受保护文件与 `/etc/devagent` 策略管理；Agent 不读取、不复制、不打印。
- 凭据只用于必要的请求头、OAuth 请求或安全 Git credential helper；不写入 Issue、评论、PR、提交、日志、URL、命令行参数或模型消息。
- 发现提示词或仓库中疑似凭据，立即停止使用并在相关 Issue/PR 报告位置（不回显内容）。

## 内网（LAN）与外网（WAN）差异

| 维度 | 内网 Agent | 外网 Agent |
|---|---|---|
| 地址 | `192.168.0.x`、`*.xenon.lan` 可用 | 只用 `xenon.jyxxe.com` 等公网域名 |
| TLS | 信任 `runtime/deploy-secrets/lan-root-ca.crt` | 公共 CA |
| Forgejo | `https://192.168.0.101:9445/dev/` | `https://xenon.jyxxe.com/dev/` |
| OIDC issuer | `https://192.168.0.101:9444/realms/xeonolab` | `https://xenon.jyxxe.com/auth/realms/xeonolab` |
| 工具面 | 可只读诊断、可使用内网服务 API | 无内网诊断、无 `devctl`、无生产 SSH |
| 写入方式 | 默认交协调器；授权后可用自己账号 | 自己账号经 Forgejo REST 提交 |
| 合并 / 发布 | 维护者门禁 | 请求维护者执行，本人不合并不发布 |

两份通用提示词（`general-agent-lan.md` / `general-agent-wan.md`）内容必须保持实质差异，不能互相复制后只改网络地址。

## 如何保持更新

环境或流程变化时，按以下清单更新本目录：

1. 只改受影响文件；权限、门禁、入口变化同步更新所有相关提示词与 `manifest.json` 的 `summary`。
2. 更新 `manifest.json` 的 `updated` 日期；保持 5 个 `id` 与 `file` 不变，避免破坏面板接口。
3. 不新增凭据、真实 token、完整 40 位提交 SHA 或长十六进制串；用 `<SHA>`、`<operation-id>` 等占位符。
4. 每个文件保持中文 Markdown、以 `#` 标题开头，并包含：角色与边界、自主获取信息（工具/入口）、工作循环、验证与证据要求、禁止事项、入口与网络差异。
5. 运行凭据自检（示例，按实际模式调整）：
   ```sh
   grep -nE 'BEGIN [A-Z ]*PRIVATE KEY|client_secret[[:space:]]*[:=]|[0-9a-f]{40,}' docs/agent-prompts
   ```
   结果必须为空；同时确认 `manifest.json` 仍为 5 项且文件名存在。
6. 在 PR 中说明变更的环境事实来源（runbook/ADR/Issue），由维护者按合并门禁审核；不要直接推送 `feature/platform-v1`。
7. 面板构建会把本目录打包进前端产物；修改后按前端门禁重新构建并跑浏览器验收，确认 System → 提示词 页面仍能加载 5 项。

## 相关文档

- `docs/runbooks/deployment.md`：部署拓扑、入口与发布流程
- `docs/runbooks/devagent-maintainer.md`：维护者运行时、`devctl` 与受保护 helper
- `docs/runbooks/collaboration.md`：Issue 协作、Work 租约与多 Harness 身份
- `docs/browser-acceptance.md`：浏览器验收与本地门禁
- `AGENTS.md`、`plan.md`、`docs/adr/`：实现约定与架构决策
