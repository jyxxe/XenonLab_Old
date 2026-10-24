# Issue #264 — 一次性 API key 未保存前的账号切换保护（2026-10-24）

- `Accounts` 在一次性 token 显示期间（`secret` 非空）拦截账号树切换：点击其他账号时不改变选择、
  保留 token 可见，并提示「当前 API key 尚未保存：请先复制并点击『已保存，关闭』后再切换账号」。
- 点击「已保存，关闭」后清除 `secret`，账号切换恢复正常；创建 key、撤销、列表等既有行为不变。
- 测试：新增 `tests/account-api-key-fixture.html/tsx` 与
  `tests/browser-acceptance-account-api-key-guard.test.mjs`（合成账号树 + 一次性 token），断言
  切换被拦截、token 保持可见、提示出现、确认后切换成功；无真实凭据写入。
