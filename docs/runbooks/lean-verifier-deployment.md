# Lean 验证 worker 部署（Issue #28 / plan §40）

`lean-verifier` 是独立服务，为 Library Agent 的 verify_request 提供有界 Lean 校验。
本 runbook 描述**生产激活**所需的沙箱、工具链与凭据边界；未按本文件部署时服务保持
`not ready`，`POST /api/v1/lean/verify` 返回 503 `VERIFIER_UNAVAILABLE`（fail-closed）。

## 1. 沙箱（必须）

使用版本化的 `infra/lean-verifier/lean-verifier.service`：

```bash
install -m 0644 infra/lean-verifier/lean-verifier.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now lean-verifier
```

关键属性（缺一不可）：

- `PrivateNetwork=yes`、`RestrictAddressFamilies=AF_UNIX`：worker 及其 Lean 子进程没有网络；
- `PrivateTmp=yes`、`ProtectSystem=strict`、`ProtectHome=yes`、`ReadOnlyPaths=/opt/lean`：
  只读工具链、仅 `ReadWritePaths` 可写；
- `NoNewPrivileges=yes`、`ProtectKernel*`、`LockPersonality=yes`、`MemoryMax`/`TasksMax`。

服务启动后自查：读取 `/proc/self/ns/net` 与 `/proc/self/ns/mnt` 的 inode，必须与
`/proc/1/ns/*` 不同；否则 readiness 保持 503。**不要**通过环境变量或关闭检查绕过。

## 2. 可信工具链 manifest

`/etc/xeonolab/lean-toolchains.json` 必须 **root:root 0644（组/其他不可写）**，每个条目
固定二进制路径与 SHA-256；请求只能按名称选择，不能提供路径：

```json
{
  "toolchains": [
    {"name": "lean4", "path": "/opt/lean/lean4/bin/lean", "sha256": "<64-hex>"}
  ]
}
```

校验规则（`load_toolchains`）：manifest 与二进制均为 root-owned 普通文件、非组/其他可写；
二进制可执行且 SHA-256 与声明一致；任一条不满足即整体 fail-closed。升级工具链必须同时更新
manifest 的 sha256 并重启服务。

## 3. 资源与保留

- 同步与排队请求共享 `LEAN_MAX_CONCURRENT`（默认 2）准入；队列上限 `LEAN_MAX_QUEUED`
  （默认 64，满时 429 `LEAN_QUEUE_FULL`）；同步忙时 429 `LEAN_BUSY`。
- 单次 walltime ≤120s、内存 2 GiB、FSIZE 64 MiB、NPROC 64 rlimit；stdout/stderr 在进程
  运行期间按 16 KiB 上限收集（超出标记 truncated，不随输出增长内存）。
- 完成作业保留最近 `LEAN_JOB_HISTORY`（默认 200）条；更早作业连同其源码目录一起清理。
  排队/运行中作业的源码保留用于重启恢复，数量受 `LEAN_MAX_QUEUED` 限制。
- 作业**按创建者隔离**：状态/取消仅 owner 可用，其他调用方 403 `JOB_FORBIDDEN`。

## 4. 明确不支持

- **依赖锁文件校验（dependency_lock）不支持**：结果显式返回
  `dependency_lock_checked: false`，不会声称校验过 lockfile。需要锁定依赖的验证必须
  在工具链镜像/manifest 层面固定，另开设计。
- 未知/未固定工具链、缺失沙箱、越过上限的请求一律失败，不回退到未沙箱执行。

## 5. 发布后验证

```bash
systemctl is-active lean-verifier
curl -sk https://192.168.0.101:9443/health/ready    # 网关聚合（若接入）
curl -s http://127.0.0.1:8107/health/ready           # 直连
# 以 lean:verify 身份提交最小源码并检查 verified/failed、exit code、axioms 与 walltime
```

真实 Lean 验收与 Library Agent 集成属于 Issue #28 的后续增量，不在本 runbook 内自动完成。
