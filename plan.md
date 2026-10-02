# PVE AI / Knowledge Platform — Initial Development Plan

> 文件：`plan.md`  
> 状态：Initial Architecture / V1 Implementation Plan  
> 目标读者：Codex、OpenCode、人工开发者、系统管理员  
> 主要实现者：Codex  
> 文档目标：给出可直接用于初期开发的系统边界、数据模型、服务拓扑、API 契约、权限模型、发布模型与阶段性验收标准。

---

## 0. 项目摘要

本项目运行在单节点 PVE（Proxmox VE）宿主机上，目标是在一台高容量主机上同时提供：

1. OpenWebUI：面向用户的 LLM 交互入口，并为模型提供 XeLibrary 查询工具。
2. Web-Server：macOS 风格桌面门户，通过 iframe 窗口承载各组件。
3. fnOS：相对独立的 NAS 服务。
4. HF-cache：Hugging Face 模型 / 数据集缓存与预测性预取服务。
5. XeLibrary：超大规模知识库，支持 `.xl` 数据表、附件、结构化表达式查询、全文检索、向量检索、定理区。
6. Embedding：只提供两种模型：
   - `D100M-Embedding`：默认、快速、全量文本编码。
   - `Qwen/Qwen3-VL-Embedding-2B`：低吞吐、高质量、多模态 / 图片编码及少量二阶段重排。
7. Library Agent：负责 XeLibrary 高级查询、结论推导、定理候选生成、证明辅助与结论查询。
8. Network Server：网络代理、Cloudflare Tunnel、统一入口、受控反向代理。
9. Docker Server：公共基础组件、认证、LLM Gateway、Deploy Gateway、监控等。
10. Dev Server：公开项目完整源代码、Issues、PR、讨论。
11. DevAgent：自主项目维护，包括开发、测试、Git 写入、PR review/merge 和受保护的指定 SHA 部署；保留 Issues 分类与任务编排。
12. 多 Agent 开发：Codex 与 OpenCode 均可开发；**生产发布由验证过的 Codex 或专用 DevAgent maintainer 通过 Deploy Gateway 完成，同一时刻只有一个有效部署租约。**

本项目 V1 的总体设计原则：

- 单机但服务边界清晰。
- 数据服务优先可恢复、可审计、可迁移。
- 模型与 Agent 永远不能通过“方便”获得超出需求的生产权限。
- `.xl` 是真实数据格式，不是空壳索引。
- D100M 是 XeLibrary 的默认文本向量模型。
- Qwen3-VL-Embedding-2B 不承担全库文本编码。
- OpenWebUI 只通过受控 Tool API 查询 XeLibrary，不直接接触 XeLibrary 数据库。
- 发布是“能力租约（lease）”，不是给 Agent 一把永久生产 SSH 私钥。
- 所有内部服务都提供健康检查与可观测性接口。
- API 从 V1 开始版本化，避免后期接口不可控增长。

---

# 1. 固定硬件条件

## 1.1 PVE 宿主机

- CPU：Intel Xeon E5-2686 v4
- RAM：128 GiB
- SSD：1 TB
- HDD：
  - 18 TB × 2，组成 mirror
  - 10 TB × 1，独立盘
- Hypervisor：Proxmox VE

> 注：10 TB 盘同时承载 8 TB fnOS 逻辑数据盘和 2 TB HF-cache 逻辑数据盘时，没有现实物理余量。V1 可以按 thin provisioning 呈现 8T + 2T，但必须设置池级水位、HF-cache 自动淘汰和容量保护；不能认为两者可以同时真实写满。

---

# 2. VM 规划

| VM | vCPU | RAM | SSD 系统盘 | 数据盘 | 主要职责 |
|---|---:|---:|---:|---:|---|
| OpenWebUI | 4C | 16G | 60G | 200G / mirror | LLM UI、XeLibrary Tool 接入 |
| Web-Server | 2C | 8G | 50G | 500G / mirror | macOS 风格桌面门户 |
| fnOS | 2C | 8G | 50G | 8T / 10T HDD | NAS |
| HF-cache | 1C | 4G | 50G | 2T / 10T HDD | HF 模型 / 数据集缓存 |
| XeLibrary | 4C | 16G | 64G | 16T / mirror + 约 96G SSD fast disk | 知识库 |
| Embedding | 8C | 16G | 50G | 无独立大数据盘 | D100M + Qwen Embedding |
| Library Agent | 2C | 8G | 50G | — | 查询与推导 Agent |
| Network Server | 2C | 8G | 50G | — | Tunnel / Proxy / Egress |
| Docker Server | 4C | 16G | 64G | — | 公共基础服务 |
| Dev | 2C | 4G | 80G | — | Forgejo / 项目源代码 |
| DevAgent | 4C | 10G | 64G | — | Issue 管理 Agent |

虚拟机 RAM 总计：114 GiB。

宿主剩余约 14 GiB，因此：

- 不允许 ZFS ARC 无限制增长。
- 不允许多个高内存后台任务同时无约束运行。
- 编码、构建、Qwen、多任务索引必须有队列和并发上限。
- 后续扩容优先级：RAM > SSD > Mirror 容量 > GPU。

---

# 3. SSD 与 HDD 布局

## 3.1 1 TB SSD

逻辑目标：

```text
1TB SSD
├── PVE root / local
├── VM system disks
│   ├── OpenWebUI 60G
│   ├── Web 50G
│   ├── fnOS 50G
│   ├── HF-cache 50G
│   ├── XeLibrary 64G
│   ├── Embedding 50G
│   ├── Library Agent 50G
│   ├── Network 50G
│   ├── Docker 64G
│   ├── Dev 80G
│   └── DevAgent 64G
└── XeLibrary fast disk ~96G
    ├── metadata DB
    ├── hot vector/index data
    ├── query/index job metadata
    └── selected hot cache
```

原则：

- 系统盘从 50G 起步。
- Embedding 不单独保留大模型盘。
- SSD 的额外空间优先给 XeLibrary 热索引与元数据。
- 不把 SSD 作为不可缺少的 HDD mirror cache 层；SSD 故障后数据层应可从 mirror 和备份恢复。

## 3.2 18T × 2 Mirror

用途：

```text
tank-safe (mirror)
├── XeLibrary logical data disk 16T
├── OpenWebUI data disk 200G
└── Web data disk 500G
```

但物理空间不能真正允许三者全部写满。

建议池级水位：

- `< 70%`：正常
- `70–80%`：预警
- `80–85%`：暂停 XeLibrary 批量导入
- `> 85%`：进入保护模式，只允许必要写入与清理
- `> 90%`：紧急容量事件

## 3.3 独立 10T HDD

```text
10T HDD
├── fnOS logical volume 8T
└── HF-cache logical volume 2T
```

优先级：

`fnOS 数据 >>> HF-cache 可重下载缓存`

HF-cache 必须在物理盘接近水位时自动停止预取并淘汰缓存。

---

# 4. 逻辑网络分区

建议至少划分四个逻辑安全域：

```text
Management Zone
    PVE / 管理接口 / 管理 SSH

Application Zone
    Web / OpenWebUI / XeLibrary / Embedding / Library Agent / HF-cache / Docker services

Development Zone
    Dev / DevAgent / Codex workers / OpenCode workers / CI

NAS Zone
    fnOS
```

若物理交换机不支持 VLAN，V1 可先在 PVE bridge + firewall 中实现。

原则：

- PVE API 不对普通应用区开放。
- Dev 区不能直接访问生产数据库。
- fnOS 不依赖 Web、XeLibrary、Agent 才能工作。
- OpenWebUI 不直接访问 PostgreSQL / Qdrant / `.xl` 文件。
- Agent 只通过明确 API 访问服务。
- 部署权限只通过 Deploy Gateway 获得。

---

# 5. 逻辑组件关系

```text
                         Internet
                            │
                    Cloudflare Tunnel
                            │
                     Network Server
                            │
          ┌─────────────────┴─────────────────┐
          │                                   │
      Web Desktop                         Dev / Forgejo
          │                                   │
  ┌───────┼─────────┐                      Issues / PR
  │       │         │                          │
OpenWebUI XeLibrary UI  Agent UI             DevAgent
  │       │         │                          │
  │       └────┬────┘                    Codex / OpenCode
  │            │                              │
  │        XeLibrary API                      │
  │            │                              ▼
  │      ┌─────┴──────┐                   Main Branch
  │      │            │                       │
  │   D100M       Qwen3-VL                    ▼
  │      │            │                  Codex Release
  │      └─ Embedding ┘                       │
  │                                      Deploy Lease
  │                                           │
  └──── XeLibrary Tools                 Deploy Gateway
                                              │
                                         Production
```

旁路服务：

```text
fnOS
└── 8T NAS
    独立认证
    不要求 Web/Agent 在线

HF-cache
└── 2T cache
    ├── D100M
    ├── Qwen
    ├── datasets
    └── predictive prefetch
```

---

# 6. 推荐代码仓库结构

```text
platform/
├── apps/
│   ├── desktop-web/
│   ├── xelibrary-web/
│   └── ops-web/
│
├── services/
│   ├── xelibrary/
│   ├── embedding/
│   ├── hf-cache/
│   ├── library-agent/
│   ├── dev-agent/
│   ├── tool-gateway/
│   ├── llm-gateway/
│   ├── deploy-gateway/
│   └── network-control/
│
├── packages/
│   ├── xl-format/
│   ├── xl-query/
│   ├── auth-client/
│   ├── api-types/
│   ├── agent-tools/
│   └── observability/
│
├── infra/
│   ├── pve/
│   ├── ansible/
│   ├── systemd/
│   ├── docker/
│   └── network/
│
├── docs/
│   ├── architecture/
│   ├── api/
│   ├── security/
│   ├── runbooks/
│   └── adr/
│
├── scripts/
├── tests/
├── AGENTS.md
├── CONTRIBUTING.md
└── plan.md
```

---

# 7. 初始技术栈

V1 优先追求开发速度和接口稳定性。

## 7.1 Python 服务

推荐使用：

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy / asyncpg
- httpx
- structlog / 标准 JSON logging

适用于：

- XeLibrary API
- Embedding API
- HF-cache
- Library Agent
- DevAgent
- Tool Gateway
- 初期 Deploy Gateway

## 7.2 Web

推荐：

- React
- TypeScript
- Vite
- 基于 CSS / Tailwind 的桌面 UI
- iframe window manager
- TanStack Query
- WebSocket / SSE client

## 7.3 数据

XeLibrary：

- `.xl`：SQLite-based application file
- 元数据库：PostgreSQL
- 向量：Qdrant
- 全文：SQLite FTS5 / 后续可替换
- 附件：内容寻址文件系统
- 热索引：SSD
- 冷数据：18T mirror

Docker Server：

- 身份服务（OIDC）
- Deploy Gateway DB
- LLM Gateway
- Prometheus / Grafana（资源允许时）

Dev：

- Forgejo

---

# 8. `.xl` 文件格式

## 8.1 基本原则

`.xl` 是真实知识表文件，而不是数据库连接描述。

一个 `.xl` 可以视为：

```text
SQLite database
+
XeLibrary metadata schema
+
user table schema
+
change journal
+
attachment references
```

建议内部结构：

```text
__xl_meta
__xl_schema
__xl_attachments
__xl_changes
__xl_stats
data
```

## 8.2 `__xl_meta`

建议字段：

```sql
format_version TEXT
table_id TEXT
table_revision INTEGER
logical_path TEXT
created_at TEXT
updated_at TEXT
created_by TEXT
schema_revision INTEGER
```

## 8.3 `__xl_schema`

保存用户字段定义：

```text
column_id
name
display_name
type
nullable
default_value
index_type
expression_permissions
metadata_json
```

支持的字段类型 V1：

- string
- text
- int64
- float64
- boolean
- datetime
- date
- enum
- json
- reference
- attachment_list
- theorem_ref

## 8.4 `data`

系统字段：

```text
_row_id
_revision
_created_at
_updated_at
_deleted_at
```

其余为用户字段。

## 8.5 大表分片

逻辑上一个表：

```text
/数学/分析/大表
```

物理上可以是：

```text
/data/tables/<table_id>/
├── manifest.json
├── part-000001.xl
├── part-000002.xl
└── part-000003.xl
```

对用户仍表现为一张表。

---

# 9. XeLibrary 附件

附件采用内容寻址存储：

```text
/data/objects/sha256/ab/cd/<full-hash>
```

`.xl` 中只记录：

```text
attachment_id
row_id
object_hash
filename
media_type
size
created_at
metadata_json
```

附件支持：

- PDF
- image
- archive
- source code
- Lean source
- raw dataset
- office file
- arbitrary binary（受策略限制）

禁止直接依赖文件名作为唯一 ID。

---

# 10. D100M-Embedding 固定参数

当前已训练完成的 `D100M-Embedding`：

- 参数量：100.85M
- 输出维度：256
- L2 normalization
- 最大长度：128 tokens
- Bidirectional attention
- Masked mean pooling
- Learned projection
- 多语言：主要包含 EN / ZH / JA / FR / ES
- 定位：紧凑型主检索模型
- 当前内部验证：
  - Held-out in-batch Recall@1：24.8% → 91.6%
  - contrastive loss：2.933 → 0.175
- 该指标不是 MTEB。

## 10.1 角色

D100M 是：

- XeLibrary 默认文本 Embedding
- 全量文本 chunk 的主向量空间
- 普通查询默认 query embedding
- Library Agent 快速检索默认模型

---

# 11. Qwen3-VL-Embedding-2B 角色

Qwen 模型只用于：

- 图片 embedding
- 多模态检索
- 用户明确要求 quality 模式
- D100M 召回后的少量候选二次编码
- 某些需要更强语义区分的高价值数据

不用于：

- 全量文本 ingestion 默认编码
- 让所有普通查询等待 2B 模型
- 与 D100M 平均分配请求

---

# 12. Chunk 设计

D100M 最大长度 128 token，因此 chunk 必须按 tokenizer 计算。

建议默认：

- 正文目标：96–112 token
- overlap：约 16 token
- hard limit：128 token
- 保留 path / title / column context token

概念形式：

```text
[PATH] 数学 / 分析
[TABLE] 一致收敛
[COLUMN] proof
[TITLE] 一致收敛与积分交换
[TEXT] ...
```

每个 chunk 唯一键：

```text
table_id
row_id
column_id
chunk_id
row_revision
chunker_version
embedding_model_revision
```

---

# 13. 向量空间

至少维护三个逻辑空间：

```text
fast_text
    D100M
    256D
    全量文本
    永久

vision
    Qwen3-VL
    图片 / 多模态
    永久

quality_cache
    Qwen3-VL
    文本候选
    按需
    可淘汰
```

不同模型的相似度不得直接混为同一分数。

---

# 14. 默认查询策略

## 14.1 Fast

```text
query
  ↓
D100M query vector
  ↓
fast_text ANN
  ↓
structured permission/filter validation
  ↓
top results
```

## 14.2 Quality

```text
query
  ↓
D100M
  ↓
Top 100~500 candidates
  ↓
Qwen query embedding
  ↓
按需编码候选 / 命中 quality_cache
  ↓
rerank / fusion
  ↓
final result
```

## 14.3 Image Search

```text
text/image query
  ↓
Qwen
  ↓
vision
  ↓
results
```

---

# 15. 定理区数据模型

建议路径：

```text
/theorems/
├── mathematics/
├── computer-science/
└── domain/
```

定理对象：

```text
theorem_id
path
title
statement_natural
assumptions
proof_natural
formal_statement
lean_source
lean_version
dependency_lock
depends_on
evidence_refs
formal_status
review_status
confidence
provenance
created_by
created_at
updated_at
revision
```

`confidence` 不等同于形式证明状态。

建议拆分：

```text
confidence.evidence_score
confidence.source_score
confidence.consistency_score
formal_status
```

`formal_status`：

- none
- proposed
- generated
- verifying
- verified
- failed
- invalidated

---

# 16. API 总体规范

所有自研 HTTP API 默认：

```text
/api/v1/...
```

模型兼容 API 可以使用：

```text
/v1/...
```

## 16.1 Content-Type

默认：

```http
Content-Type: application/json
```

文件上传允许：

```http
multipart/form-data
application/octet-stream
```

## 16.2 Authentication

用户请求：

```http
Authorization: Bearer <OIDC access token>
```

服务间：

- OIDC service account token
- 或 mTLS
- 禁止靠来源 IP 作为唯一认证

## 16.3 用户上下文传递

OpenWebUI 调用 XeLibrary 时：

- 模型不能看到用户 access token。
- OpenWebUI / Tool Gateway 在服务端持有并转发或交换 token。
- XeLibrary 根据真实用户权限做授权。
- 禁止用 `X-User: alice` 一类可伪造头部代替身份验证。

## 16.4 Request ID

所有请求：

```http
X-Request-ID: <uuid>
```

没有时由服务生成。

响应：

```json
{
  "data": {},
  "meta": {
    "request_id": "..."
  }
}
```

## 16.5 错误格式

统一：

```json
{
  "error": {
    "code": "XL_TABLE_NOT_FOUND",
    "message": "table not found",
    "details": {},
    "request_id": "..."
  }
}
```

HTTP 状态码仍正常使用：

- 400 invalid request
- 401 unauthenticated
- 403 forbidden
- 404 not found
- 409 conflict
- 412 precondition failed
- 413 too large
- 422 semantic validation failed
- 429 rate limited
- 500 internal
- 503 unavailable

## 16.6 乐观并发

修改类接口使用：

```http
ETag: "revision:18"
If-Match: "revision:18"
```

版本不一致：

```http
412 Precondition Failed
```

## 16.7 Idempotency

创建 / 部署 / 批量写入建议支持：

```http
Idempotency-Key: <uuid>
```

---

# 17. 通用运维 API

所有自研服务统一：

```text
GET /health/live
GET /health/ready
GET /version
GET /metrics
```

可选：

```text
GET /debug/config-summary
GET /debug/dependencies
```

`/debug/*` 仅管理员可访问。

---

# 18. Web-Server API

Web-Server 只负责“桌面壳”和组件组织。

## 18.1 应用目录

```text
GET  /api/v1/apps
GET  /api/v1/apps/{app_id}
POST /api/v1/apps                  admin only
PATCH /api/v1/apps/{app_id}        admin only
DELETE /api/v1/apps/{app_id}       admin only
```

应用对象（这里只登记独立来源的外部应用；内置应用由桌面客户端提供）：

```json
{
  "id": "external-app",
  "title": "External app",
  "origin": "https://app.example.internal",
  "entry": "/",
  "icon": "library",
  "default_size": [1200, 800],
  "singleton": false,
  "permissions": ["xelibrary:read"],
  "sandbox": {
    "allow_scripts": true,
    "allow_forms": true,
    "allow_downloads": false
  }
}
```

历史外部 `xelibrary` 应用入口已由桌面内置的“知识库”替代。不得再由部署验收脚本或默认配置创建该外部入口；遗留 registry 记录通过桌面管理员的应用目录 API 删除，用户布局中的旧 dock/window/recent 引用由客户端迁移到内置 `knowledge` 应用。此入口清理不删除 XeLibrary 服务、API、数据或反向代理路由。

## 18.2 用户桌面布局

```text
GET /api/v1/users/me/layout
PUT /api/v1/users/me/layout
```

内容：

```json
{
  "dock": ["openwebui", "xelibrary", "dev"],
  "windows": [
    {
      "app_id": "xelibrary",
      "x": 40,
      "y": 48,
      "width": 1200,
      "height": 760,
      "minimized": false
    }
  ]
}
```

## 18.3 桌面偏好

```text
GET   /api/v1/users/me/preferences
PATCH /api/v1/users/me/preferences
```

## 18.4 iframe 消息协议

顶层和 iframe 使用 `postMessage`，定义：

```text
desktop.ready
app.ready
app.request_resize
app.set_title
app.open_app
app.notify
auth.session_changed
theme.changed
```

所有消息都必须验证 `event.origin`。

---

# 19. OpenWebUI → XeLibrary Tool API

这是模型访问 XeLibrary 的主要入口。

V1 推荐由独立 `Tool Gateway` 提供 OpenAI function-style JSON API，避免 OpenWebUI 直接调用 XeLibrary 内部管理接口。

## 19.1 Tool Catalog

```text
GET /api/v1/tools
```

返回模型可用工具及 JSON Schema。

## 19.2 工具：`xelibrary.search`

用途：自然语言 / 语义搜索。

```text
POST /api/v1/tools/xelibrary.search
```

请求：

```json
{
  "query": "一致收敛和积分交换",
  "path": "/数学/**",
  "mode": "fast",
  "limit": 20,
  "filters": {
    "language": "zh"
  }
}
```

`mode`：

- fast
- quality
- auto

## 19.3 工具：`xelibrary.query`

用途：高级表达式查询。

```text
POST /api/v1/tools/xelibrary.query
```

```json
{
  "expression": "FROM \"/数学/**\" WHERE type = \"theorem\" AND confidence.evidence_score >= 80 LIMIT 50"
}
```

## 19.4 工具：`xelibrary.get`

```text
POST /api/v1/tools/xelibrary.get
```

```json
{
  "table_id": "...",
  "row_id": "...",
  "revision": 12,
  "fields": ["title", "body", "source"]
}
```

## 19.5 工具：`xelibrary.get_attachment`

```text
POST /api/v1/tools/xelibrary.get_attachment
```

默认返回受限下载令牌 / 预览信息，不直接把大型二进制塞进模型上下文。

## 19.6 工具：`xelibrary.theorem_search`

```text
POST /api/v1/tools/xelibrary.theorem_search
```

```json
{
  "query": "dominated convergence theorem",
  "domain": "/theorems/mathematics/**",
  "formal_status": ["verified", "none"],
  "limit": 20
}
```

## 19.7 工具：`xelibrary.derive`

交给 Library Agent。

```text
POST /api/v1/tools/xelibrary.derive
```

```json
{
  "question": "在这些条件下能否交换极限与积分？",
  "path": "/数学/**",
  "require_formal": false,
  "max_evidence": 40
}
```

返回异步任务 ID。

## 19.8 工具：`xelibrary.propose_change`

默认关闭。

仅当未来允许模型提交知识库变更建议时开放：

```text
POST /api/v1/tools/xelibrary.propose_change
```

只创建 proposal，不直接修改正式数据。

---

# 20. XeLibrary Catalog / Path API

## 20.1 路径

```text
GET    /api/v1/paths
POST   /api/v1/paths
GET    /api/v1/paths/{path_id}
PATCH  /api/v1/paths/{path_id}
DELETE /api/v1/paths/{path_id}
```

查询：

```text
GET /api/v1/paths?parent=/数学/分析
```

## 20.2 路径移动

```text
POST /api/v1/paths/{path_id}/move
```

```json
{
  "new_parent": "/数学/实分析"
}
```

移动逻辑路径不改变 table_id。

---

# 21. XeLibrary Table API

```text
GET    /api/v1/tables
POST   /api/v1/tables
GET    /api/v1/tables/{table_id}
PATCH  /api/v1/tables/{table_id}
DELETE /api/v1/tables/{table_id}
```

## 21.1 创建表

```json
{
  "path": "/数学/分析",
  "name": "一致收敛",
  "columns": [
    {"name": "title", "type": "string", "nullable": false},
    {"name": "body", "type": "text", "nullable": false},
    {"name": "confidence", "type": "float64", "nullable": true}
  ]
}
```

## 21.2 获取 schema

```text
GET /api/v1/tables/{table_id}/schema
```

## 21.3 修改 schema

```text
PATCH /api/v1/tables/{table_id}/schema
```

需要 schema revision。

## 21.4 表移动

```text
POST /api/v1/tables/{table_id}/move
```

## 21.5 表统计

```text
GET /api/v1/tables/{table_id}/stats
```

包含：

- rows
- deleted rows
- storage bytes
- attachment bytes

## 21.6 删除表

`DELETE /api/v1/tables/{table_id}` 仅接受带 `xelibrary:admin` scope 的请求，并要求
`If-Match` 等于当前表 revision。表必须没有行（含软删除行）、附件引用、向量引用、运行中的
索引任务或进行中的上传；否则返回 `409` 并保留表。需要清空内容时，先按既有行 API purge
每一行，再解除附件引用，确认索引工作结束后重试。删除会移除表文件、catalogue 项及表/行 ACL，
并写入独立审计记录。内容寻址对象不会因删表而直接 unlink，以免破坏其他表引用；未引用对象的
垃圾回收仍需单独实现。跨文件删除使用可恢复 tombstone，XeLibrary 启动时恢复未提交删除或完成
已提交删除的清理。
- chunk count
- embedding coverage
- index status

---

# 22. XeLibrary Row API

```text
GET    /api/v1/tables/{table_id}/rows
POST   /api/v1/tables/{table_id}/rows
GET    /api/v1/tables/{table_id}/rows/{row_id}
PATCH  /api/v1/tables/{table_id}/rows/{row_id}
DELETE /api/v1/tables/{table_id}/rows/{row_id}
```

## 22.1 列表

```text
GET /api/v1/tables/{id}/rows?limit=100&cursor=...
```

## 22.2 创建

```json
{
  "values": {
    "title": "一致收敛",
    "body": "...",
    "confidence": 0.92
  }
}
```

## 22.3 更新

必须带 `If-Match`。

## 22.4 软删除

默认 DELETE 为软删除。

永久删除需要：

```text
DELETE /api/v1/tables/{table_id}/rows/{row_id}?purge=true
```

仅管理员。

---

# 23. XeLibrary Bulk API

```text
POST /api/v1/tables/{table_id}/rows:bulkInsert
POST /api/v1/tables/{table_id}/rows:bulkUpdate
POST /api/v1/tables/{table_id}/rows:bulkDelete
```

大批量写入返回 job：

```json
{
  "data": {
    "job_id": "job_..."
  }
}
```

---

# 24. XeLibrary Import / Export API

## 24.1 导入

```text
POST /api/v1/imports
GET  /api/v1/imports/{job_id}
POST /api/v1/imports/{job_id}/cancel
```

支持：

- CSV
- JSONL
- `.xl`
- zip package
- future: Parquet

## 24.2 导出

```text
POST /api/v1/exports
GET  /api/v1/exports/{job_id}
GET  /api/v1/exports/{job_id}/download
```

导出模式：

- xl-only
- xl-with-attachments
- jsonl
- csv
- theorem-package

---

# 25. XeLibrary Attachment API

## 25.1 上传

小文件：

```text
POST /api/v1/attachments
```

大文件建议分片：

```text
POST /api/v1/uploads
PUT  /api/v1/uploads/{upload_id}/parts/{part}
POST /api/v1/uploads/{upload_id}/complete
DELETE /api/v1/uploads/{upload_id}
```

分片上传的创建请求为 JSON：`{ "table_id", "row_id", "filename", "size" }`；`part` 从 1 开始，分片内容使用 `application/octet-stream`。当前实现每片最多 8 MiB、单文件最多 2 GiB，单主体最多 3 个活动会话，主体预留上限为 4 GiB、全局预留上限为 20 GiB。会话绑定创建者和目标行，24 小时后过期；重复提交相同分片或重复完成是幂等的，重复分片内容不一致会冲突。完成前不会创建附件引用，取消会删除未完成分片。会话状态保存于 `DATA_DIR/uploads.sqlite`，分片和完成锁暂存于 `DATA_DIR/objects/.uploads/`；备份需将两处与现有 `.xl` 和对象存储一并纳入快照。

## 25.2 附件查询

```text
GET /api/v1/attachments/{attachment_id}
GET /api/v1/attachments/{attachment_id}/content
GET /api/v1/attachments/{attachment_id}/preview
```

## 25.3 绑定 / 解绑

```text
POST   /api/v1/tables/{table_id}/rows/{row_id}/attachments
DELETE /api/v1/tables/{table_id}/rows/{row_id}/attachments/{attachment_id}
```

## 25.4 内容寻址去重

上传完成后计算 SHA-256。

同 hash 的对象复用底层存储，但 ACL 仍按业务引用检查。

---

# 26. XeLibrary Query DSL

V1 建议语法：

```text
FROM <path-pattern>
[WHERE <expr>]
[SEARCH TEXT <query> [USING <fast|quality|auto>]]
[ORDER BY <expr> [ASC|DESC]]
[LIMIT <n>]
[OFFSET <n>]
```

例：

```text
FROM "/数学/**"
WHERE kind = "theorem"
  AND confidence >= 0.8
  AND EXISTS(attachments WHERE media_type = "application/pdf")
SEARCH TEXT "一致收敛 积分交换" USING fast
ORDER BY relevance DESC
LIMIT 30
```

## 26.1 运算符

比较：

```text
=
!=
<
<=
>
>=
```

集合：

```text
IN
NOT IN
CONTAINS
CONTAINS ANY
CONTAINS ALL
```

文本：

```text
STARTS_WITH
ENDS_WITH
MATCHES
```

空值：

```text
IS NULL
IS NOT NULL
```

逻辑：

```text
AND
OR
NOT
```

关系：

```text
EXISTS(...)
```

## 26.2 内置函数

V1 可实现：

```text
LOWER()
UPPER()
LEN()
COALESCE()
DATE()
YEAR()
MONTH()
HAS_ATTACHMENT()
PATH_DEPTH()
```

后续：

```text
REGEX()
JSON_GET()
DISTANCE()
SIMILARITY()
```

## 26.3 禁止

- `eval`
- arbitrary Python
- arbitrary JavaScript
- 任意系统命令
- 用户拼接原始 SQL

DSL 必须先 parse 成 AST，再转换到受控执行计划。

---

# 27. XeLibrary Query API

```text
POST /api/v1/query
POST /api/v1/query/explain
POST /api/v1/query/validate
```

## 27.1 query

```json
{
  "expression": "FROM \"/数学/**\" WHERE confidence >= 0.8 LIMIT 100",
  "cursor": null
}
```

## 27.2 explain

返回：

- selected shards
- structured filters
- full-text stage
- vector stage
- expected approximate/exact
- estimated cost class

## 27.3 validate

只做 parse / type check / permission check，不执行。

---

# 28. XeLibrary Search API

## 28.1 semantic

```text
POST /api/v1/search/semantic
```

```json
{
  "query": "一致收敛与积分交换",
  "path": "/数学/**",
  "mode": "fast",
  "limit": 30,
  "filters": {}
}
```

## 28.2 hybrid

```text
POST /api/v1/search/hybrid
```

组合：

- structured filter
- FTS
- vector

## 28.3 text

```text
POST /api/v1/search/text
```

The legacy English FTS5/BM25 index remains unchanged. A separate CJK-tokenized
FTS index supports unspaced Chinese keyword substrings. CJK queries also run a
bounded literal scan over readable active rows to cover legacy `.xl` files and
merge/deduplicate those hits with indexed results. ACL, path and field filters
are rechecked against current `.xl` data. When the scan budget leaves rows
unchecked, the response marks `partial=true` and `approximate=true`; the default
budget is 5,000 rows (`XELIBRARY_CJK_SCAN_ROWS`, capped at 100,000). See
[ADR 0006](docs/adr/0006-xelibrary-cjk-text-search.md) for index migration and
rollback compatibility.

## 28.4 image

```text
POST /api/v1/search/image
```

请求可以上传图片或使用附件 ID。

## 28.5 related

```text
GET /api/v1/rows/{row_id}/related
```

---

# 29. Search Result 统一返回字段

```json
{
  "table_id": "...",
  "row_id": "...",
  "row_revision": 18,
  "path": "/数学/分析",
  "score": 0.84,
  "score_type": "d100m_cosine",
  "snippet": "...",
  "matched_chunks": [
    {
      "chunk_id": "...",
      "column": "body"
    }
  ],
  "approximate": true,
  "indexed_revision": 18
}
```

若结果存在索引滞后：

```text
partial = true
missing_embeddings > 0
```

---

# 30. XeLibrary Index API

普通用户不可见；管理员 / worker 使用。

```text
GET  /api/v1/index/status
GET  /api/v1/index/jobs
POST /api/v1/index/reindex
POST /api/v1/index/rebuild-table
POST /api/v1/index/rebuild-row
POST /api/v1/index/compact
POST /api/v1/index/pause
POST /api/v1/index/resume
```

## 30.1 Reindex

```json
{
  "table_id": "...",
  "model": "d100m",
  "priority": "background"
}
```

---

# 31. XeLibrary Revision API

```text
GET  /api/v1/tables/{table_id}/revisions
GET  /api/v1/tables/{table_id}/rows/{row_id}/revisions
GET  /api/v1/tables/{table_id}/rows/{row_id}/revisions/{revision}
POST /api/v1/tables/{table_id}/rows/{row_id}/restore
```

restore 创建新 revision，不直接抹掉历史。

---

# 32. XeLibrary ACL API

角色 / scope 建议：

```text
xelibrary:read
xelibrary:query
xelibrary:write
xelibrary:attach
xelibrary:theorem:read
xelibrary:theorem:propose
xelibrary:theorem:promote
xelibrary:admin
```

接口：

```text
GET  /api/v1/acl
GET  /api/v1/acl/{resource_type}/{resource_id}
PUT  /api/v1/acl/{resource_type}/{resource_id}
```

支持继承：

```text
/path ACL
  ↓
table ACL
  ↓
row ACL（仅必要时）
```

---

# 33. XeLibrary Proposal API

模型与 Agent 默认通过 proposal 写入。

```text
POST /api/v1/proposals
GET  /api/v1/proposals
GET  /api/v1/proposals/{id}
POST /api/v1/proposals/{id}/approve
POST /api/v1/proposals/{id}/reject
POST /api/v1/proposals/{id}/supersede
```

proposal 可包含：

- create row
- update row
- delete row
- new theorem
- theorem proof update
- schema suggestion

---

# 34. Theorem API

```text
GET  /api/v1/theorems
POST /api/v1/theorems
GET  /api/v1/theorems/{id}
PATCH /api/v1/theorems/{id}
```

普通 Agent 不直接获得 PATCH 正式定理权限。

## 34.1 Search

```text
POST /api/v1/theorems/search
```

## 34.2 Candidate

```text
POST /api/v1/theorems/{id}/candidates
```

## 34.3 Verify

```text
POST /api/v1/theorems/{id}/verify
```

## 34.4 Promote

```text
POST /api/v1/theorems/{id}/promote
```

需要高权限 / 审核策略。

## 34.5 Dependencies

```text
GET /api/v1/theorems/{id}/dependencies
GET /api/v1/theorems/{id}/dependents
```

## 34.6 Evidence

```text
GET  /api/v1/theorems/{id}/evidence
POST /api/v1/theorems/{id}/evidence
```

---

# 35. Embedding API

Embedding 服务只维护两个模型：

```text
fast    = D100M-Embedding
quality = Qwen3-VL-Embedding-2B
```

## 35.1 模型列表

```text
GET /v1/models
```

示例：

```json
{
  "data": [
    {
      "id": "D100M-Embedding",
      "alias": "fast",
      "type": "text",
      "dimensions": 256,
      "normalized": true,
      "max_tokens": 128,
      "status": "ready"
    },
    {
      "id": "Qwen/Qwen3-VL-Embedding-2B",
      "alias": "quality",
      "type": "multimodal",
      "dimensions": "configured",
      "status": "ready"
    }
  ]
}
```

## 35.2 OpenAI-compatible text embedding

```text
POST /v1/embeddings
```

请求：

```json
{
  "model": "fast",
  "input": [
    "第一段文本",
    "第二段文本"
  ]
}
```

返回：

```json
{
  "object": "list",
  "model": "D100M-Embedding",
  "data": [
    {
      "index": 0,
      "embedding": [0.01, 0.02]
    }
  ],
  "usage": {
    "input_tokens": 42
  }
}
```

## 35.3 高级 text API

```text
POST /api/v1/embed/text
```

支持：

- priority
- truncation policy
- explicit revision
- batch metadata

## 35.4 Image Embedding

```text
POST /api/v1/embed/image
```

支持：

- multipart image
- XeLibrary attachment ID
- internal object ref

## 35.5 Multimodal

```text
POST /api/v1/embed/multimodal
```

## 35.6 异步大批量

```text
POST   /api/v1/embed/jobs
GET    /api/v1/embed/jobs/{id}
POST   /api/v1/embed/jobs/{id}/cancel
GET    /api/v1/embed/jobs/{id}/results
```

## 35.7 Queue

管理员：

```text
GET /api/v1/admin/queue
GET /api/v1/admin/workers
```

## 35.8 Model reload

```text
POST /api/v1/admin/models/{model}/reload
```

不允许普通 Agent 调用。

---

# 36. Embedding 调度优先级

固定默认：

```text
1. D100M interactive
2. D100M ingestion
3. Qwen image interactive
4. Qwen quality rerank
5. Qwen background task
```

实现上：

```text
embedding-api
├── d100m-worker × N
└── qwen-worker × 1
```

Qwen 队列不能阻塞 D100M。

---

# 37. HF-cache API

## 37.1 Cache Item

资源唯一键：

```text
repo_type
repo_id
revision
allow_patterns
ignore_patterns
```

## 37.2 Ensure

```text
POST /api/v1/cache/ensure
```

```json
{
  "repo_type": "model",
  "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
  "revision": "main",
  "priority": "production",
  "pin": true
}
```

## 37.3 Prefetch

```text
POST /api/v1/cache/prefetch
```

## 37.4 List

```text
GET /api/v1/cache/items
GET /api/v1/cache/items/{id}
```

## 37.5 Pin

```text
POST   /api/v1/cache/items/{id}/pin
DELETE /api/v1/cache/items/{id}/pin
```

## 37.6 Evict

```text
POST /api/v1/cache/items/{id}/evict
```

禁止淘汰：

- pinned
- in-use
- unique internal model artifact

## 37.7 GC

```text
POST /api/v1/cache/gc
```

参数：

```json
{
  "target_usage_percent": 75
}
```

## 37.8 Disk status

```text
GET /api/v1/cache/storage
```

## 37.9 Usage Lease

服务加载模型时：

```text
POST   /api/v1/cache/items/{id}/leases
DELETE /api/v1/cache/items/{id}/leases/{lease_id}
```

防止正在使用的模型被 GC。

---

# 38. HF-cache 预测性 API

## 38.1 Dependency signal

```text
POST /api/v1/signals/dependency
```

来源：

- Forgejo source scan
- deployment manifest
- requirements
- config
- Issue
- task queue

示例：

```json
{
  "source": "forgejo",
  "repository": "platform",
  "commit": "...",
  "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
  "confidence": 0.98,
  "reason": "referenced in production embedding config"
}
```

## 38.2 Candidate list

```text
GET /api/v1/prefetch/candidates
```

## 38.3 Accept / Reject

```text
POST /api/v1/prefetch/candidates/{id}/accept
POST /api/v1/prefetch/candidates/{id}/reject
```

生产依赖可自动 accept；Issue 文本提及默认不能直接触发大下载。

---

# 39. Library Agent API

Library Agent 负责“需要推理的知识任务”。

## 39.1 创建任务

```text
POST /api/v1/tasks
```

类型：

```text
query
derive
compare
theorem_candidate
proof_candidate
formalize
verify_request
evidence_check
contradiction_check
```

## 39.2 获取任务

```text
GET /api/v1/tasks/{id}
```

## 39.3 取消

```text
POST /api/v1/tasks/{id}/cancel
```

## 39.4 SSE

```text
GET /api/v1/tasks/{id}/events
```

Content-Type：

```text
text/event-stream
```

事件：

```text
task.started
search.started
search.result
reasoning.stage
proof.generated
lean.started
lean.result
task.completed
task.failed
```

注意：对外事件不输出模型私有 chain-of-thought，只输出可审计的任务阶段、证据、结果和工具调用摘要。

## 39.5 Derive

便捷接口：

```text
POST /api/v1/derive
```

## 39.6 Theorem candidate

```text
POST /api/v1/theorems/propose
```

最终落到 XeLibrary proposal。

---

# 40. Lean 验证接口

可作为 Library Agent 的内部模块或独立 worker。

```text
POST /api/v1/lean/verify
GET  /api/v1/lean/jobs/{id}
POST /api/v1/lean/jobs/{id}/cancel
```

请求：

```json
{
  "toolchain": "...",
  "source": "...",
  "dependency_lock": "...",
  "network": false,
  "timeout_seconds": 60
}
```

返回：

```json
{
  "status": "verified",
  "stdout": "...",
  "stderr": "",
  "axioms": []
}
```

执行环境：

- 无生产密钥
- 无 PVE API
- 默认禁网
- CPU / RAM / walltime 限制
- 临时工作目录

---

# 41. LLM Gateway API

Docker Server 上提供统一生成模型入口。

目的：

- 不让每个 Agent 自己保存多个 Provider Key
- 审计模型调用
- 统一限流
- 将来可切换本地 / 外部模型

可提供 OpenAI-compatible：

```text
POST /v1/chat/completions
POST /v1/responses
GET  /v1/models
```

内部管理：

```text
GET  /api/v1/providers
GET  /api/v1/usage
GET  /api/v1/limits
```

不向模型暴露 Provider Secret。

---

# 42. Network Server API

原则：Network Server 不提供“任意 URL fetch”给 Agent，避免变成 SSRF / 内网扫描入口。

## 42.1 Status

```text
GET /api/v1/network/status
```

## 42.2 Tunnel

管理员：

```text
GET  /api/v1/tunnels
GET  /api/v1/tunnels/{id}
POST /api/v1/tunnels/{id}/restart
```

## 42.3 Routes

配置应优先 GitOps。

可提供：

```text
GET /api/v1/routes
```

写入：

```text
POST /api/v1/routes/validate
```

V1 不建议 Agent 直接动态创建公网路由。

## 42.4 Connectivity Test

受控：

```text
POST /api/v1/connectivity/test
```

只能测试 allowlist 目标。

---

# 43. Forgejo 集成

Dev Server 使用 Forgejo。

项目不重复实现完整 Git / Issue API。

主要使用 Forgejo 原生接口实现：

- repositories
- branches
- commits
- contents
- issues
- issue comments
- labels
- milestones
- pull requests
- reviews
- webhooks
- commit statuses
- releases

自研 DevAgent 只调用受限范围的 Forgejo token。

---

# 44. DevAgent API

## 44.1 Webhook

```text
POST /api/v1/webhooks/forgejo
```

接收：

- issue opened
- issue edited
- issue comment
- PR opened
- PR merged
- label changed

必须验证 webhook signature。

## 44.2 Issue Triage

```text
POST /api/v1/issues/{issue_id}/triage
```

输出：

```json
{
  "labels": ["xelibrary", "bug"],
  "priority": "normal",
  "duplicates": [41],
  "related": [17, 32],
  "recommended_agent": "codex"
}
```

## 44.3 Duplicate

```text
POST /api/v1/issues/{issue_id}/mark-duplicate
```

只执行：

- 添加标签
- 评论
- 关联原 Issue
- 可关闭

不删除历史。

## 44.4 Link

```text
POST /api/v1/issues/{issue_id}/links
```

类型：

```text
duplicate_of
related_to
blocked_by
blocks
supersedes
```

## 44.5 Work Queue

```text
GET /api/v1/work
POST /api/v1/work/{id}/claim
POST /api/v1/work/{id}/release
```

## 44.6 Suggested Patch

AI 代码建议最终必须变成：

- Issue comment
- branch
- commit
- PR

不能直接改 main。

---

# 45. 多 Agent 身份模型

每个 Agent 必须有独立身份：

```text
codex-dev-01
codex-dev-02
codex-dev-03
opencode-01
dev-agent
library-agent
codex-release
```

禁止多个 Agent 共用一个超级 token。

权限：

| 身份 | Repo Read | Branch Write | PR | Issue | Production Deploy |
|---|---:|---:|---:|---:|---:|
| Codex Dev | yes | own branch | yes | yes | no |
| OpenCode | yes | own branch | yes | yes | no |
| DevAgent autonomous maintainer | yes | own branch | write/review/merge | yes | **protected helper + lease required** |
| Library Agent | source optional | no | no | limited | no |
| Codex Release | yes | no/limited | read | read | **lease required** |

---

# 46. Deploy Gateway

目标：

> 发布由验证过的 Codex 或专用 DevAgent maintainer 身份通过 Deploy Gateway 完成；任意时刻只有一个有效 holder。

Agent 永远不直接获得长期：

- PVE root token
- production SSH private key
- Docker socket
- database superuser password

Deploy Gateway 持有受限执行凭据。

---

# 47. Deploy Lease API

## 47.1 当前租约

```text
GET /api/v1/deploy/lease
```

## 47.2 获取

```text
POST /api/v1/deploy/lease/acquire
```

请求：

```json
{
  "agent_id": "codex-dev-02",
  "release_id": "rel_...",
  "ttl_seconds": 60
}
```

服务端验证：

- identity 是 Codex 或专用 DevAgent maintainer（签名 agent_kind=devagent，仍需 deploy:execute scope）
- release 已通过策略
- 当前无有效 holder
- 当前无未解决 deployment state

返回：

```json
{
  "lease_id": "...",
  "epoch": 87,
  "expires_at": "...",
  "holder": "codex-dev-02"
}
```

## 47.3 Renew

```text
POST /api/v1/deploy/lease/{lease_id}/renew
```

## 47.4 Release

```text
POST /api/v1/deploy/lease/{lease_id}/release
```

## 47.5 Force clear

仅人工管理员：

```text
POST /api/v1/admin/deploy/lease/force-clear
```

需要审计原因。

---

# 48. Release API

## 48.1 注册 release

```text
POST /api/v1/releases
```

```json
{
  "commit": "<sha>",
  "artifacts": [
    {
      "name": "xelibrary",
      "digest": "sha256:..."
    }
  ],
  "migration_manifest": "...",
  "test_report": "...",
  "created_by": "codex-dev-01"
}
```

## 48.2 查询

```text
GET /api/v1/releases
GET /api/v1/releases/{id}
```

## 48.3 Approve

若策略要求人工批准：

```text
POST /api/v1/releases/{id}/approve
```

只允许人类 / policy service。

---

# 49. Deployment API

## 49.1 部署

```text
POST /api/v1/deployments
```

请求：

```json
{
  "release_id": "rel_...",
  "environment": "production",
  "lease_id": "...",
  "epoch": 87
}
```

## 49.2 状态

```text
GET /api/v1/deployments/{id}
```

## 49.3 Events

```text
GET /api/v1/deployments/{id}/events
```

SSE。

## 49.4 Cancel

```text
POST /api/v1/deployments/{id}/cancel
```

仅在安全阶段允许。

## 49.5 Rollback

```text
POST /api/v1/deployments/{id}/rollback
```

仍要求有效 lease。

---

# 50. 部署状态机

```text
created
  ↓
validated
  ↓
preparing
  ↓
deploying
  ↓
health_check
  ├── success → completed
  └── failure → rollback_pending
                  ↓
              rolling_back
                  ↓
          rolled_back / failed
```

租约过期不自动认为旧部署已停止。

必须先由执行器确认：

- 旧任务完成
- 或已停止
- 或已被 fencing

否则新租约冻结。

---

# 51. Deploy Gateway 内部执行

Codex 只调用 Deploy Gateway。

Deploy Gateway 内部可以：

```text
Ansible
systemd
Docker Compose
PVE API
restricted SSH
```

V1 推荐：

- VM 内部服务使用 Ansible + systemd / Docker Compose
- VM 创建 / 硬件变更使用 PVE 管理流程
- 普通应用部署不要每次调用 PVE 改 VM

---

# 52. `deployctl` CLI

Codex 使用：

```bash
deployctl lease acquire --release rel_x
deployctl lease status
deployctl deploy rel_x
deployctl status dep_x
deployctl logs dep_x
deployctl rollback dep_x
deployctl lease release
```

CLI 只是 Deploy Gateway 客户端，不包含生产私钥。

---

# 53. `xlctl` CLI

人工 / 管理员 / Codex 可使用：

```bash
xlctl table list
xlctl table inspect <id>
xlctl query '<expr>'
xlctl import ...
xlctl export ...
xlctl index status
xlctl index rebuild ...
xlctl theorem inspect ...
```

必须经过 XeLibrary API，不直接修改 `.xl` 文件。

---

# 54. `hf-cachectl`

```bash
hf-cachectl status
hf-cachectl ensure model Qwen/Qwen3-VL-Embedding-2B
hf-cachectl pin ...
hf-cachectl gc
hf-cachectl candidates
```

---

# 55. OpenWebUI 权限原则

OpenWebUI 本身不是 XeLibrary 超级管理员。

模型工具默认权限：

```text
read
query
search
theorem_search
derive_request
```

默认没有：

```text
row_write
row_delete
acl_change
schema_change
theorem_promote
index_admin
```

未来允许写入时只开放 proposal。

---

# 56. Web / iframe 安全

每个应用有独立 origin。

桌面域：

```text
desk.example.com
```

应用：

```text
chat.example.com
library.example.com
dev.example.com
```

受控应用明确允许桌面嵌入。

iframe 消息必须：

- 检查 origin
- 检查 message type
- 不接受任意 JS 字符串执行
- 不通过 query string 传永久 token
- 不允许 iframe 获取 PVE 管理 Cookie

---

# 57. 身份服务

V1 不自研登录系统。

统一依赖 OIDC。

最少角色：

```text
user
developer
agent
operator
admin
```

服务 scope 单独定义，不直接等于 UI role。

---

# 58. Secrets

禁止提交到 Forgejo：

- production API keys
- Cloudflare token
- PVE token
- SSH private keys
- DB root password
- LLM provider secret
- HF restricted model token

推荐：

- Docker Server secret store
- root-only file
- systemd credentials
- 后续可迁移 Vault

---

# 59. 审计 API / Audit Log

关键系统必须写审计：

- ACL change
- schema change
- row purge
- theorem promote
- Agent proposal approve
- release approve
- lease acquire / release
- deployment
- rollback
- secret rotation

接口：

```text
GET /api/v1/audit/events
GET /api/v1/audit/events/{id}
```

普通用户只能查看与自身相关部分。

---

# 60. Job API 统一规范

长任务统一：

```text
POST /... → job_id
GET /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
GET /api/v1/jobs/{job_id}/events
```

Job 状态：

```text
queued
running
cancel_requested
completed
failed
cancelled
```

---

# 61. Event / Webhook 设计

V1 可以先用 HTTP + DB outbox。

后续若引入 NATS / JetStream，可使用以下 topic：

```text
xelibrary.table.created
xelibrary.table.moved
xelibrary.row.created
xelibrary.row.updated
xelibrary.row.deleted
xelibrary.attachment.created
xelibrary.index.requested
xelibrary.index.completed
xelibrary.theorem.proposed
xelibrary.theorem.verified

embedding.job.started
embedding.job.completed
embedding.job.failed

hfcache.item.ready
hfcache.item.evicted
hfcache.storage.warning

dev.issue.created
dev.issue.updated
dev.issue.duplicate
dev.pr.opened
dev.pr.merged

deploy.lease.acquired
deploy.lease.released
deploy.started
deploy.completed
deploy.failed
deploy.rollback.started
deploy.rollback.completed
```

所有事件包含：

```json
{
  "event_id": "...",
  "type": "...",
  "occurred_at": "...",
  "source": "...",
  "actor": "...",
  "resource": {},
  "request_id": "..."
}
```

---

# 62. Webhook 重试规则

Webhook receiver 必须支持：

- signature 验证
- idempotency
- event_id 去重
- 最少一次投递语义
- 重试安全

禁止假设 webhook 只会收到一次。

---

# 63. 监控

所有服务提供 Prometheus metrics。

最少指标：

## XeLibrary

```text
xelibrary_query_total
xelibrary_query_duration_seconds
xelibrary_query_errors_total
xelibrary_rows_total
xelibrary_chunks_total
xelibrary_embedding_backlog
xelibrary_index_lag
xelibrary_storage_bytes
```

## Embedding

```text
embedding_requests_total
embedding_latency_seconds
embedding_queue_depth
embedding_batch_size
embedding_model_loaded
embedding_errors_total
```

分别标记：

```text
model="d100m"
model="qwen3vl2b"
```

## HF-cache

```text
hfcache_usage_bytes
hfcache_download_bytes_total
hfcache_hit_total
hfcache_miss_total
hfcache_evicted_bytes_total
```

## Deploy

```text
deploy_lease_active
deploy_duration_seconds
deploy_failure_total
deploy_rollback_total
```

---

# 64. 日志

统一 JSON logging：

```json
{
  "timestamp": "...",
  "level": "INFO",
  "service": "xelibrary",
  "request_id": "...",
  "actor_id": "...",
  "event": "query.completed",
  "duration_ms": 32
}
```

禁止日志中记录：

- bearer token
- secret
- provider API key
- password
- 敏感全文（除非显式调试并受控）

---

# 65. OpenTelemetry

V1 可以只保留：

- request_id
- structured logs
- metrics

P2 再加入：

- distributed trace
- W3C traceparent
- OpenTelemetry collector

---

# 66. XeLibrary 内部一致性

写入顺序：

```text
1. 修改 .xl row
2. 同一个 SQLite 事务写 __xl_changes
3. commit
4. index worker 读取 change
5. chunk
6. D100M embedding
7. update vector index
8. update FTS
9. mark indexed_revision
```

向量索引不是事实源。

事实源：

```text
.xl + metadata + attachment store
```

---

# 67. 删除语义

删除 row：

- 正式数据立刻对用户不可见。
- vector 清理由异步 worker 完成。
- 查询 API 最后仍检查当前 row state。
- 因此旧向量暂存不会造成已删除数据返回给用户。

---

# 68. 权限与向量查询

不能只依赖 vector payload ACL。

正确流程：

```text
user
 ↓
resolve allowed scope
 ↓
vector/fts candidates
 ↓
current ACL + row state validation
 ↓
return
```

严格过滤场景必须避免“topK 后过滤导致结果不足却误认为真实 topK”。

---

# 69. fnOS 边界

fnOS：

- 独立 VM
- 独立数据盘
- 独立认证
- 不要求 Web Desktop 在线
- 不要求 Docker Server 在线
- 不要求 XeLibrary 在线

V1 不定义 fnOS 自研 API。

若以后需要和平台集成，只提供：

- 状态读取
- 只读容量
- 受控文件分享

不把 fnOS 管理权限交给普通 Agent。

---

# 70. 备份

必须区分：

- mirror = 可用性
- backup = 恢复

需要备份：

```text
PVE config
Forgejo repos / issues DB
OIDC DB
Deploy Gateway DB
XeLibrary metadata DB
.xl
attachment store
D100M final model
Qwen pinned revision metadata
critical configs
```

通常不全量备份：

```text
re-downloadable HF public cache
rebuildable vector index
rebuildable FTS
temporary quality cache
```

---

# 71. XeLibrary 恢复顺序

```text
1. metadata DB
2. .xl files
3. attachment objects
4. ACL
5. consistency scan
6. rebuild FTS
7. rebuild D100M vectors
8. rebuild / warm hot index
```

---

# 72. HF-cache 水位

由于 8T + 2T 共用 10T 盘，必须做物理盘级判断。

建议：

```text
< 80%
    normal

80% ≤ used < 85%
    stop speculative prefetch

85% ≤ used < 90%
    evict low-priority

≥ 90%
    retain only pinned / in-use
```

fnOS 数据优先。

---

# 73. 开发权限

任何开发者 / Agent 可以：

- 查看完整公开源代码
- 查看 Issues
- 回复 Issues
- 提 PR

禁止：

- 直接 push main
- 直接修改 production
- 在 Issue 文字中携带“执行命令”就获得高权限
- 让 CI PR runner 持有生产 secret

---

# 74. Codex 开发规则

Codex 初期为主要实现者。

每个 Codex：

- 独立分支
- 独立 worktree / workspace
- 独立 agent identity
- 通过 PR 合并
- 不直接修改其他 Agent 工作目录

建议：

```text
feature/xelibrary-query
feature/embedding-api
feature/desktop-windowing
fix/issue-123
```

---

# 75. Codex 发布规则

生产发布允许 Codex 和专用 DevAgent maintainer 身份，统一使用独占租约与受保护执行器。

但不是：

```text
所有 Codex 永远持有生产权限
```

而是：

```text
任意时刻
最多一个 Codex / DevAgent maintainer
持有短期 deploy lease
```

lease 失效后必须通过 Deploy Gateway 的执行状态确认旧发布已结束，才能交给下一 Agent。

---

# 76. OpenCode 角色

OpenCode 可以：

- 独立实现
- Review
- 实验方案
- PR
- Issue 建议

不能：

- 获得 production deploy lease
- 持有生产 credential

---

# 77. 初始开发阶段

## Phase 0 — Infrastructure

完成：

- PVE VM
- 网络分区
- 磁盘
- DNS
- Cloudflare Tunnel
- OIDC 基础
- Forgejo
- 基础监控

验收：

- 所有 VM 独立启动
- fnOS 不依赖业务组件
- Dev 无生产凭据
- 系统盘均 >= 50G

---

# 78. Phase 1 — Repo / API Skeleton

创建：

```text
services/*
packages/api-types
packages/auth-client
```

实现所有服务：

```text
/health/live
/health/ready
/version
```

定义 OpenAPI。

验收：

- CI 可启动所有服务 skeleton
- API error schema 统一
- request_id 可贯穿

---

# 79. Phase 2 — XeLibrary Core

实现：

- `.xl` format v1
- table
- row
- schema
- path
- attachment
- import/export
- revision
- ACL
- Query DSL parser

暂不先做 Agent。

验收：

- 创建 `.xl`
- 写入 / 更新 / 删除
- 崩溃恢复
- schema 修改
- path move 不破坏 table_id
- query DSL 不允许执行任意代码

---

# 80. Phase 3 — D100M Search

实现：

- D100M service
- tokenizer-aware chunking
- vector schema
- Qdrant
- ingestion worker
- semantic search
- hybrid search

验收：

- 全链路 row → chunk → embedding → query
- embedding revision 可追踪
- 删除 row 后不会被旧 vector 泄露
- D100M 在线请求优先于后台 ingestion

---

# 81. Phase 4 — Qwen / Multimodal

实现：

- Qwen worker
- image embedding
- vision index
- quality rerank
- quality cache

验收：

- Qwen 请求不能阻塞 D100M
- image search 可用
- quality 模式只对候选做二次编码

---

# 82. Phase 5 — Web / OpenWebUI

实现：

- desktop shell
- app registry
- iframe window manager
- OpenWebUI integration
- Tool Gateway
- `xelibrary.search/query/get/theorem_search`

验收：

- 用户权限能透传到 XeLibrary
- 模型不能获得管理员 token
- iframe origin policy 正确

---

# 83. Phase 6 — Library Agent

实现：

- task API
- evidence retrieval
- derive
- theorem candidate
- Lean sandbox
- proposal

验收：

- Agent 不能直接改正式 theorem
- 推导结果带 evidence
- formal_status 与 confidence 分离

---

# 84. Phase 7 — DevAgent

DevAgent V1 的定位为 autonomous project maintainer，运行在 VM111 的独立 Unix 用户上，使用 OpenCode CLI 和 `opencode-go/deepseek-v4.1-flash`。现有 triage/work API 保留；增量 watcher、reconciliation timer、SQLite 去重、人工 CLI、Forgejo helper 和 root-owned deployment/infrastructure helper 承担自主维护。它可开发、测试、commit/push、PR write/review/merge 和指定 SHA 部署。一般 OpenCode Harness 的旧生产权限限制仍保留；专用 maintainer 通过签名身份和受保护接口获得权限。

验收和权限交接必须基于实际运行证据，不能以配置存在代替完成。见 `docs/runbooks/devagent-maintainer.md`。

实现：

- Forgejo webhook
- issue triage
- duplicate
- links
- work queue

验收：

- 不删除原 Issue
- 不直接修改 main
- Prompt injection 不能变成生产命令

---

# 85. Phase 8 — Deploy Gateway

实现：

- release
- lease
- deploy
- rollback
- SSE
- audit

验收：

- 同时只有一个 lease
- OpenCode 无法 acquire
- 第二个 Codex 无法在 lease 已占用时部署
- lease 过期不自动并发启动第二次部署
- 所有生产操作都有审计

---

# 86. Phase 9 — HF-cache

实现：

- cache ensure
- pin
- lease
- GC
- storage watermark
- dependency signal
- speculative candidate

验收：

- 模型已缓存时 Embedding 无需公网重新下载
- HF-cache 不在线时已部署模型仍可启动
- 水位高时自动停止预测预取

---

# 87. API 测试矩阵

每个 endpoint 至少测试：

```text
success
401
403
404
409
412
422
429
500
idempotency
retry
permission inheritance
```

写入 API 额外测试：

```text
concurrent update
stale revision
duplicate request
partial failure
```

---

# 88. Query 测试

必须包含：

- 中文
- 英文
- 日文
- path wildcard
- typed comparison
- null
- nested boolean
- attachment EXISTS
- FTS
- D100M semantic
- quality rerank
- permission filter
- stale index
- deleted row
- moved table

---

# 89. D100M 评测

训练集内部 Recall@1 不作为生产结论。

V1 建立本领域 eval：

```text
query
relevant rows
hard negatives
language
domain
```

记录：

- Recall@10
- Recall@50
- nDCG
- MRR
- latency
- throughput
- failure rate

比较：

- FTS only
- D100M
- D100M + FTS
- D100M + Qwen rerank

---

# 90. API 稳定性

V1 发布后：

- `/api/v1` 保持兼容。
- 破坏性修改进入 `/api/v2`。
- 字段新增默认向后兼容。
- 不复用旧字段表达新语义。
- 所有客户端使用生成的 OpenAPI types 或共享 `api-types`。

---

# 91. OpenAPI

所有服务维护：

```text
/openapi.json
/docs
```

生产环境 `/docs` 可限制访问。

仓库内生成：

```text
docs/api/<service>.openapi.json
```

CI 检查 breaking change。

---

# 92. API 客户端

共享包：

```text
packages/api-types/
packages/xelibrary-client/
packages/embedding-client/
packages/deploy-client/
```

避免每个 Agent 自己猜接口。

---

# 93. 数据 ID

推荐 UUIDv7 / sortable ID。

对象：

```text
table_id
row_id
attachment_id
theorem_id
proposal_id
job_id
release_id
deployment_id
lease_id
event_id
```

不要让用户可修改路径充当主键。

---

# 94. 时间

API 一律：

```text
RFC3339 / UTC
```

例如：

```text
2026-09-30T05:35:00Z
```

前端按用户时区显示。

---

# 95. 分页

统一 cursor：

```text
GET /...?limit=100&cursor=...
```

不鼓励大表使用 offset。

响应：

```json
{
  "data": [],
  "meta": {
    "next_cursor": "..."
  }
}
```

---

# 96. Rate Limit

建议至少分类：

```text
interactive
bulk
admin
agent
embedding-fast
embedding-quality
```

Qwen 单独严格限流。

---

# 97. 上传限制

所有上传：

- MIME sniff
- size limit
- filename sanitize
- hash
- quarantine / optional scan
- no execution

附件 preview 与运行环境隔离。

---

# 98. 不允许的架构捷径

禁止：

1. OpenWebUI 直接连 XeLibrary DB。
2. Agent 直接读写 `.xl` 文件。
3. Agent 直接拿 PVE root token。
4. Agent 直接访问 Docker socket。
5. 多个 Codex 共用一个生产 SSH key。
6. OpenCode 获得生产 deploy lease。
7. Qwen 承担全量文本默认 embedding。
8. 使用字符串拼接生成 SQL。
9. 使用 `eval()` 执行 Query DSL。
10. 将向量索引当作事实源。
11. 将 mirror 当作 backup。
12. 让 PR runner 持有 production credentials。
13. 将 Issue 文本当作可信系统指令。
14. 通过 URL query 参数传长期 access token。
15. 因为 `.xl` 有文件路径就使用路径作为唯一 ID。

---

# 99. V1 最低可用接口集合

如果要最快形成 MVP，第一批只实现：

## XeLibrary

```text
POST /api/v1/tables
GET  /api/v1/tables
GET  /api/v1/tables/{id}
POST /api/v1/tables/{id}/rows
GET  /api/v1/tables/{id}/rows/{row_id}
PATCH /api/v1/tables/{id}/rows/{row_id}
POST /api/v1/query
POST /api/v1/search/semantic
POST /api/v1/attachments
GET  /health/*
```

## Embedding

```text
GET  /v1/models
POST /v1/embeddings
POST /api/v1/embed/image
GET  /health/*
```

## OpenWebUI tools

```text
xelibrary.search
xelibrary.query
xelibrary.get
```

## DevAgent

```text
POST /api/v1/webhooks/forgejo
POST /api/v1/issues/{id}/triage
```

## Deploy

```text
POST /api/v1/releases
POST /api/v1/deploy/lease/acquire
POST /api/v1/deploy/lease/{id}/renew
POST /api/v1/deploy/lease/{id}/release
POST /api/v1/deployments
GET  /api/v1/deployments/{id}
```

这样即可形成最小闭环。

---

# 100. 推荐首批 Issues

建议 Codex 初始建立以下 Issues：

```text
#1  Define common API conventions
#2  Create service skeletons
#3  Define .xl v1 format
#4  Implement XeLibrary metadata DB
#5  Implement path/table/row API
#6  Implement attachment content-addressed store
#7  Implement Query DSL parser
#8  Implement D100M embedding service
#9  Implement tokenizer-aware chunker
#10 Integrate Qdrant fast_text collection
#11 Implement semantic search
#12 Implement Web Desktop shell
#13 Implement app registry
#14 Implement OpenWebUI XeLibrary tool gateway
#15 Implement Forgejo webhook receiver
#16 Implement DevAgent triage
#17 Implement release manifest
#18 Implement deploy lease
#19 Implement deployment state machine
#20 Add audit logging
#21 Add HF-cache ensure/pin/gc
#22 Add Qwen worker
#23 Add image search
#24 Add quality reranking
#25 Add theorem data model
#26 Add Library Agent task API
#27 Add Lean sandbox verifier
#28 Add backup/restore runbook
#29 Add metrics and dashboards
#30 Perform failure-injection test
```

---

# 101. 初始 Codex 工作方式

Codex 每次开始任务前：

1. 读取 `plan.md`
2. 读取 `AGENTS.md`
3. 读取相关 ADR
4. 检查 Issue
5. 建独立分支
6. 修改
7. 运行测试
8. 更新 API schema / docs
9. 创建 PR
10. 由 CI / Review 决定是否合并

如果任务影响架构：

```text
docs/adr/ADR-xxxx-*.md
```

先写 ADR，再大改代码。

---

# 102. Definition of Done

一个功能只有满足以下条件才视为完成：

- 有实现
- 有单元测试
- 有集成测试
- 有权限测试
- 有错误处理
- 有日志
- 有 metrics（关键路径）
- API 已写 OpenAPI
- 文档已更新
- 不泄露 secret
- 不绕过 deploy policy
- 不引入未说明的生产依赖

---

# 103. V1 成功标准

系统达到以下状态即可认为 V1 架构成立：

1. 用户通过 Web Desktop 打开 OpenWebUI / XeLibrary。
2. OpenWebUI 模型可通过 Tool API 查询 XeLibrary。
3. XeLibrary 可以建立 `.xl` 表、保存附件、执行结构化查询。
4. D100M 可对全量文本进行默认 embedding。
5. Qwen 只负责图片与 Quality 二阶段查询。
6. Library Agent 可在只读知识数据上完成推导并提交 proposal。
7. Forgejo 公开完整项目源代码和 Issues。
8. DevAgent 可以自动分类 / 去重 / 关联 Issues。
9. Codex 与 OpenCode 可同时开发。
10. 只有验证过的 Codex 或专用 DevAgent maintainer 身份可以请求生产 deploy lease。
11. 任意时刻只有一个有效 deploy lease。
12. 发布、回滚、ACL、定理晋升均可审计。
13. fnOS 在 AI / Web / Agent 全部停机时仍可独立访问。
14. HF-cache 可自动缓存，但磁盘压力下优先保护 fnOS。
15. `.xl` 与正式模型权重可以从独立备份恢复。
16. 向量索引与 HF 公共缓存可重建，不被当作唯一事实数据。

---

# 104. 后续可扩展项

不属于初始必做，但架构应预留：

- MCP Adapter：让 Codex / OpenCode 通过标准工具协议读取 XeLibrary。
- 分布式 XeLibrary shard。
- 单独 GPU Embedding node。
- 独立 vector node。
- 多 PVE node。
- object storage。
- remote backup。
- NATS / JetStream。
- 更强形式化证明调度。
- 自动 schema migration。
- 多租户知识库。
- dataset lineage。
- package / artifact registry。
- staging 环境。
- canary deployment。
- blue/green deployment。
- richer desktop IPC。
- offline local LLM inference。

---

# 105. 最终架构原则

整个项目的核心不是“在 PVE 上启动很多 VM”，而是建立四条清晰边界：

### 数据边界

`.xl + attachments + metadata` 是事实数据；索引都是可重建派生物。

### 模型边界

D100M 是快速主模型；Qwen 是低吞吐的多模态 / quality 补充。

### Agent 边界

Agent 通过 API 工作，不直接获得基础设施超级权限。

### 发布边界

开发可以多 Agent 并行；生产发布始终通过 Deploy Gateway，并且任意时刻只有一个 Codex / DevAgent maintainer 持有有效部署租约。

只要这四条边界不被破坏，后续无论增加 GPU、SSD、第二台 PVE、更多 Agent 或更大的知识库，都可以在不推翻系统的前提下继续扩展。
