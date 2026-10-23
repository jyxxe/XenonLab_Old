# Issue #152 — isolated PNG attachment preview（2026-10-23）

本增量交付 #152 的 **PNG 隔离预览**，PDF 预览仍按 Issue 讨论保持未启用（依赖决策待定，
继续使用鉴权下载回退）。

## 后端

`GET /api/v1/attachments/{aid}/preview?table_id=...`（scope `xelibrary:read`）：

- 复用下载相同的账户/表/行 ACL 与行存在性检查；
- 只接受服务端上传时按文件头识别为 `image/png` 的附件（文件名/请求 MIME 不被信任），
  其他类型 415 `PREVIEW_UNSUPPORTED`；
- 用 Pillow 完整解码并**重新编码**为新的 RGBA PNG，源文件的文本/EXIF/ICC chunk
  不会到达浏览器；解码失败 422 `PREVIEW_DECODE_FAILED`；
- 限制 8 MiB、4096px/边、800 万像素、单帧（超限 413 `PREVIEW_TOO_LARGE`），先用头尺寸
  检查再 `load()`，避免大图内存放大；
- 响应 `nosniff`、`no-store`、`Content-Security-Policy: default-src 'none'; sandbox`。

## 前端

Inspector 附件面板：仅对 `image/png` 显示「预览」按钮，点击后经鉴权拉取预览副本并用本地
blob URL 展示在模态框（`role=dialog`），关闭/卸载时 `revokeObjectURL`；PDF 与未知类型保持
「鉴权下载」，不做活动内容渲染。策略集中在 `attachment-preview.js`（`canPreviewAttachment`）。

## 测试

- 后端：`tests/test_attachment_preview.py`（元数据剥离、尺寸保持、重编码可解码、PDF 415、
  损坏 PNG 422、无读权限/缺失 404 或 403）。
- 前端：`attachment-preview.test.mjs`（仅精确 PNG 类型可预览）。
- 浏览器验收：XeLibrary 验收在附件面板点击 PNG「预览」，断言 blob URL、图片解码、dialog 语义、
  请求命中 `/attachments/att-2/preview`，并确认 PDF 项没有「预览」按钮；关闭后模态消失。
- 全量：723 pytest、Ruff、OpenAPI 重新导出（新增 preview 路径）、前端单测/build 与完整浏览器回归。

## 未包含 / 后续

- **PDF 隔离预览**：Issue #152 的讨论指出需要 PDF 解析/栅格化依赖或独立 preview origin 的
  架构决策，本增量不引入新依赖；决定后单独实现并保留下载回退。
- 真实登录态生产浏览器验收（需人工登录）不在本地合成 fixture 范围内。
