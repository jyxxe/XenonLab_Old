# XeonoLab

按 [plan.md](plan.md) 建立的初始 V1 实现。**当前是已上线 PVE 的可运行开发版本，尚未完成全部阶段。**
已实现真实 `.xl` 数据文件、行 CRUD/历史/乐观并发/创建幂等、权限继承、附件内容寻址、
安全 DSL 子集、全文搜索、真实 D100M CPU 推理、token 分块、本地精确向量索引、
只读 Tool Gateway、部署租约、签名 webhook、LLM 转发及 React 桌面窗口。

## 开发

```bash
uv sync
uv run pytest --basetemp runtime/test-tmp
uv run ruff check xeonolab tests scripts
uv run python scripts/export_openapi.py
cd apps/desktop-web
npm ci
npm run build
```

分别启动服务：

```bash
SERVICE=xelibrary uv run uvicorn xeonolab.services:create_app --factory --host 127.0.0.1 --port 8100
SERVICE=desktop-web uv run uvicorn xeonolab.services:create_app --factory --host 127.0.0.1 --port 8101
```

默认生产模式，受保护 API 必须有经过 RSA 签名、issuer/audience/expiry 校验的 OIDC token。
提供 `OIDC_ISSUER`、`OIDC_AUDIENCE`、`OIDC_PUBLIC_KEY`，具体 scopes 见各 OpenAPI。
开发测试可显式设置 `APP_MODE=development` 和 `DEV_TOKENS_JSON`，例如：
`{"local-test":{"sub":"local-user","scope":"xelibrary:read xelibrary:write xelibrary:query desktop:read"}}`。
此令牌仅用于隔离的本机开发环境，不用于部署。令牌不存储在浏览器 localStorage 或 URL。

## D100M

```bash
uv sync --extra inference
D100M_MODEL_PATH=/path/to/D100M-Embedding SERVICE=embedding \
  uv run uvicorn xeonolab.services:create_app --factory --host 127.0.0.1 --port 8104
D100M_MODEL_PATH=/path/to/D100M-Embedding uv run python scripts/smoke_d100m.py
```

模型不提交进 Git。`D100M_MODEL_PATH` 指向包含权重、配置与 tokenizer 的文件夹。
CPU worker 每次最多 32 条，忙时返回 429，不虚构 embedding；默认拒绝超过 128 tokens 的输入。
XeLibrary 设置 `EMBEDDING_URL`、独立的 `EMBEDDING_SERVICE_TOKEN`、`D100M_TOKENIZER_PATH` 后，
管理员调用 `/api/v1/index/reindex` 创建索引，然后调用 `/api/v1/search/semantic`。
修改行后需重新索引；旧版本向量会被过滤，响应报告缺失索引数量。当前精确索引适用于开发，
超大规模生产检索仍需 Qdrant 与持久化 ingestion 队列。

## 桌面与部署

`infra/network/nginx.conf` 展示前端静态文件和 API 的同源反向代理。
用 desktop 管理 API 注册独立应用 origin。内网侧请使用 IP 入口（如 `https://192.168.0.101:9443`）；`*.lan` 主机名正在按 Issue #293 分阶段弃用，新配置只使用 IP 地址。
桌面窗口支持拖动、调整大小、最小化、最大化和保存布局，iframe 消息验证 origin 与 source。
Embedding 的本人索引任务提供“在知识库中打开”：需要 `xelibrary:read`，通过现有知识表 API 重新验证读取权限；表不存在或 ACL 已撤销时显示错误并清除上一张表的内容。
LAN DNS 与 TLS 证书仍需配置；按用户要求暂不配置 Cloudflare Tunnel。

`infra/docker/compose.yaml` 是同机开发/验收拓扑，服务仅绑定 localhost、以非 root 运行，
没有 Docker socket 或 PVE credential。生产 VM 分区不能用这个 Compose 文件替代。
Deploy Gateway 已实现审批、单租约、epoch 和未结束任务冻结；实际执行器未安装，部署返回 503。

详细完成情况与剩余工作见 [实施状态](docs/implementation-status.md)，
PVE 实测资源见 [盘点记录](docs/runbooks/pve-inventory.md)。没有配置远端 Git，尚未建立 Forgejo PR。

当前入口和登录配置见 [上线记录](docs/runbooks/deployment.md)。
