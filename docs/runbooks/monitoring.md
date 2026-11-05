# 监控运行手册（plan.md §63）

本手册描述 V1 已实现的指标、抓取方式、仪表盘与告警，以及尚未实现的部分。
所有内容不包含凭据；生产部署仍必须通过 Deploy Gateway。

## 已实现

- 每个服务在 `/metrics` 暴露 Prometheus 文本格式指标（`xeonolab/common.py` 的最小注册表：
  counter/gauge/histogram，标签转义，动态 gauge provider）。
- 通用指标（所有服务）：
  - `xeonolab_requests_total{service}`、`xeonolab_errors_total{service}`（保持原名称与语义）；
  - `xeonolab_route_requests_total{service,route,method,status}`、`xeonolab_request_duration_seconds{...}`；
  - `xeonolab_build_info{service,version}`；`route="unmatched"` 用于未匹配路径（避免高基数）。
- 领域指标（plan.md §63）：
  - XeLibrary：`xelibrary_query_total`、`xelibrary_query_duration_seconds`、`xelibrary_query_errors_total`、
    `xelibrary_rows_total`、`xelibrary_chunks_total`、`xelibrary_embedding_backlog`、`xelibrary_index_lag`、
    `xelibrary_storage_bytes`、`xelibrary_snapshot_success`。状态 gauges 由同一个受锁保护的快照读取，
    快照 60 秒刷新一次；读取失败时省略状态样本并将 `xelibrary_snapshot_success` 置为 0，
    不把数据库故障记作零行或零积压。唯一按零处理的表缺失是尚未初始化的可选 `__xl_vectors`；
    storage 遍历中仅忽略已并发消失文件的 `FileNotFoundError`，其他文件系统错误令快照失败。
    `index_lag` 定义为「行 revision 大于该行最近已索引 revision 的非删除行数」。
  - Embedding：`embedding_requests_total{model,result}`、`embedding_latency_seconds{model}`、
    `embedding_errors_total{model,code}`、`embedding_batch_size{model}`、
    `embedding_model_loaded{model}`、`embedding_queue_depth{model}`、`embedding_in_flight{model}`。
    V1 没有排队队列：`queue_depth` 始终为 0；单 worker 正在推理时由 `in_flight` 表示 1，
    并发请求直接返回 429；意外推理异常仍返回 HTTP 500，同时以固定 `code="INTERNAL_ERROR"`
    计入 `embedding_errors_total`；Qwen 未配置时两项都为 0。
  - Deploy：`deploy_lease_active`、`deploy_duration_seconds`（执行器实际耗时）、
    `deploy_failure_total`、`deploy_rollback_total`、`deploy_deployments{state}`。
  - HF-cache：当前 skeleton 仅暴露真实的 `hfcache_usage_bytes`（缓存目录字节数）；
    download/hit/miss/evicted 计数需等 #22 的缓存实现落地，先不伪造。

## 抓取与仪表盘

- `infra/monitoring/prometheus.yml`：参考抓取配置（含各 VM 端口与防火墙说明）。
- `infra/monitoring/alerts.yml`：参考告警规则（服务不可达、5xx 比例、索引积压/落后、
  部署失败、模型未加载）。
- `infra/monitoring/grafana-dashboard.json`：XeonoLab V1 仪表盘（18 个面板，使用
  `DS_PROMETHEUS` 数据源变量），导入 Grafana 时选择对应 Prometheus 数据源。
- 本地验证：
  ```bash
  SERVICE=xelibrary APP_MODE=development uv run uvicorn xeonolab.services:create_app --factory --port 8100
  curl -s localhost:8100/metrics | head
  ```

## 公开/内网入口采样（边缘链路，只读）

`scripts/public_probe.py` 对一组 URL 做 N 次 GET 采样，输出成功比例、状态码分布、中位/p95/最大延迟与失败详情，
用于区分「链路慢」和「边缘 5xx」（#126/#67），不依赖 Cloudflare account API：

```sh
uv run python scripts/public_probe.py \
  --url https://xenon.jyxxe.com/ --url https://192.168.0.101/ \
  --url https://chat.jyxxe.com/ --url https://chat.xenon.jyxxe.com/ \
  --ca-file /etc/devagent/lan-root-ca.crt --samples 10 --interval 2
```

- 退出码 0 = 全部 2xx/3xx；1 = 有失败；`--json` 输出机读报告；
- CA 在系统根证书基础上追加（可同时采样公网与 LAN 自签入口），不发送任何凭据；
- 2026-11-05 基线（6 次采样，间隔 2s）：公网桌面 **4/6 200、2/6 TLS 握手超时（11–15s）**、中位 2.1s；
  LAN 桌面 **6/6 200、中位 4ms**；`chat.jyxxe.com` 530/超时、`chat.xenon.jyxxe.com` 全部 TLS 握手失败
  （二级子域无证书）。该基线表明公网桌面的问题表现为**间歇性 TLS 握手超时/高延迟**，与 #126 描述一致；
- 原始报告按轮次保存在维护主机私有 state 目录（`state/public-probe-*.json`，无凭据）。

## 公网隧道连接诊断（cloudflared，只读）

公网入口经 VM101 的 `cloudflared.service` 隧道；连接器运行参数（2026-11-05 核对）：

```
/usr/bin/cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:20241 --protocol http2 run --token-file /etc/cloudflared/tunnel-token
```

只读诊断：

```sh
# 指标（HA 连接数、代理到源站错误、按状态码响应计数）
sudo -n curl -s http://127.0.0.1:20241/metrics | grep -E 'cloudflared_tunnel_(ha_connections|request_errors|response_by_code)'
# 边缘连接掉线事件（每次掉线后连接器会自动重连，服务本身通常不重启）
sudo -n journalctl -u cloudflared --since '24 hours ago' --no-pager | grep -E 'Lost connection with the edge|Connection terminated'
```

2026-11-05 观测：`ha_connections=4`、`request_errors=3`（自 11-01 启动以来）、24h 内 24 次边缘连接掉线
（多为单条连接，自动重连秒级完成；`systemctl show -p NRestarts` 为 0）。与 `public_probe` 观测到的公网
TLS 握手超时（11–15s，LAN 4ms 正常）时间窗相邻，指向**公网出口/边缘链路质量**而非源站应用错误。

### 为什么强制 `--protocol http2`

Cloudflare 默认优先 QUIC（UDP 7844）。本机对该出口做过一次**附加式**诊断（另起一个短时连接器，不替换现有
service；共享同一 tunnel token，Cloudflare 侧按 HA 连接分发，20s 后自动退出）：

```sh
sudo -n timeout -s TERM 20 /usr/bin/cloudflared tunnel --no-autoupdate \
  --metrics 127.0.0.1:20242 --protocol quic run --token-file /etc/cloudflared/tunnel-token
```

结果：2/4 条 QUIC 连接注册成功，另 2 条 `failed to dial to edge with quic: timeout: no recent network activity`，
precheck 输出 **`UDP Connectivity ... FAIL` / `Allow outbound QUIC traffic on port 7844 or use HTTP2`**。
因此当前 `http2` 是有依据的回退选择，**在确认 WAN/防火墙放行出站 UDP/TCP 7844 之前不要切换 QUIC**；
若后续放行，可评估 `--protocol auto` 并用上述指标/掉线率对比验证。

注意：诊断会短暂增加 HA 连接（不影响现有 service），但不要用临时连接器替代正式 unit；任何 unit/token 变更
都应走维护窗口并准备回滚（`systemctl restart cloudflared.service`）。

### 定时采样（VM111，10 分钟窗口）

`infra/systemd/xeonolab-public-probe.{service,timer}` + `/usr/local/libexec/xeonolab-public-probe`
把上面的采样做成 10 分钟一次的后台记录（仅 VM111 维护主机，只读、不写目标）：

```sh
# 从发布树安装（VM111，root）
sudo install -m 755 /opt/xeonolab/current/infra/systemd/public-probe.sh /usr/local/libexec/xeonolab-public-probe
sudo install -m 644 /opt/xeonolab/current/infra/systemd/xeonolab-public-probe.service /etc/systemd/system/
sudo install -m 644 /opt/xeonolab/current/infra/systemd/xeonolab-public-probe.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xeonolab-public-probe.timer
sudo systemctl start xeonolab-public-probe.service

systemctl list-timers xeonolab-public-probe.timer
tail -n 1 /var/lib/devagent/logs/public-probe.jsonl
```

- 每轮 4 个端点 × 6 次（间隔 2s），JSON 报告逐行追加到 `/var/lib/devagent/logs/public-probe.jsonl`
  （超过 5 MiB 自动轮转一份为 `.1`）；stderr 记录在 `public-probe.log`；
- 采样失败不会被当作 unit 失败（退出码 0；报告里的 `ok=false` 才是数据），避免每 10 分钟产生 failed unit；
- 该日志用于区分“链路慢”和“边缘 5xx/握手超时”，为 #126/#67 提供连续证据；外部告警通道仍未实现。

## 安全边界与依赖

- `/metrics` 保持现有访问边界：仅监听服务节点端口，由防火墙限制到 Prometheus 主机；
  不新增公网入口，不经过 Caddy 转发。
- 无新增外部运行时依赖；Prometheus/Grafana/Alertmanager 的部署与资源分配是运营决策，
  不在本实现内自动安装。
- 生产发布：配置与代码变更仍走 release → approval → lease → fixed executor，不得绕过。

## 未实现（不得从本手册推断完成）

- OpenTelemetry 分布式追踪 / W3C traceparent（plan.md §65 的 P2 项）。
- 告警的 Alertmanager 路由与值班集成；指标长期存储与容量规划。
- Qwen 质量模型、HF-cache 下载/命中/淘汰、Library Agent 领域指标（随对应 Issue）。
- 进程级内存/CPU/GPU 指标（当前仅暴露服务自身状态；由系统层 exporter 承担）。
