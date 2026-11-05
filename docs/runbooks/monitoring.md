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
