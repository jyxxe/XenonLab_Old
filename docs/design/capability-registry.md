# Capability Registry MVP（Epic #305 / Phase 1 第一步）

只读聚合，不改变现有插件/应用 API 的行为：

- 声明式清单 `infra/plugins/builtin-capabilities.json`（`api_version=1`）描述当前内置能力：
  app/widget/provider/worker/tool 共 12 项；能力 id 必须位于清单命名空间内。
- `xeonolab/capabilities.py` 的 `CapabilityRegistry` 校验并索引清单：api_version、semver、
  命名空间、重复 id、kind/runtime 枚举、permission 形态、依赖环；清单缺失/损坏返回 503
  （`CAPABILITY_REGISTRY_UNAVAILABLE` / `CAPABILITY_REGISTRY_INVALID`），fail closed。
- 只读端点（`desktop:read`）：`GET /api/v1/capabilities`（含 `meta.registry` 摘要）、
  `GET /api/v1/capabilities/{id}`；LAN/公网边缘新增 `/api/v1/capabilities*` 路由。
- 现有 `GET /api/v1/plugins`、`/api/v1/apps`、Dock/LaunchPad 不变；Phase 1 后续把内置应用与
  已安装插件统一到该注册表，并提供停用/兼容性检查。
- 本增量不执行任何插件代码；`ui`/`api`/`service` 仍只是声明，运行时隔离按 ADR 0009 执行。
