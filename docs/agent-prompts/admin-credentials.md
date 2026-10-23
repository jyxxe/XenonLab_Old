# 管理员凭据版提示词（运维说明）

公开提示词（`docs/agent-prompts/`）随桌面构建打包，**不含任何凭据**。
需要含真实凭据的提示词时，管理员可以把它们放到服务端受保护目录，登录 Xenon
Panel 的管理员账号在「Agent 提示词」应用的「管理员凭据版」区域查看、复制：

| 项 | 值 |
| --- | --- |
| 服务端目录 | `/etc/xeonolab/agent-prompts`（可用 `AGENT_PROMPTS_DIR` 覆盖） |
| 权限 | 目录 `root:xeonolab 0750`；文件 `root:xeonolab 0640` |
| 文件规则 | 仅普通 `*.md` 文件，≤ 64 KiB，最多 32 个；符号链接与其他后缀被忽略 |
| 接口 | `GET /api/v1/agent-prompts`，需要 `desktop:admin` 权限；未授权返回 403 |
| 面板 | Xenon Panel → 「Agent 提示词」应用（Dock/LaunchPad 入口）→ 管理员凭据版 |
| 仓库 | 这些文件**永远不进入 Git、发布包、Issue、PR、日志或聊天输出** |

## 运维步骤

```bash
# 在桌面服务所在主机（VM101）以 root 执行
install -d -o root -g xeonolab -m 0750 /etc/xeonolab/agent-prompts
install -o root -g xeonolab -m 0640 /path/to/codex-admin.md /etc/xeonolab/agent-prompts/
```

服务无需重启：每次请求实时读取目录。验证：

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://192.168.0.101:9443/api/v1/agent-prompts   # 未登录应为 401/403
```

以管理员登录面板后，应用内应列出这些文件，并显示「含真实凭据、不得外传」的警示。

## 安全边界

- 面板只对 `desktop:admin` 返回内容；普通账号只会看到说明，不返回文件正文。
- 文件内容不经过构建、不写入仓库、不进入静态资源目录。
- 管理员复制/外传即视为泄露；轮换凭据后请同步更新服务端文件。
