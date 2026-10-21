import React from 'react';
import {Button} from './design-system/primitives';

/**
 * Launch surface for the DeepSeek Harness maintenance UI.
 *
 * The shell only registers this app when the desktop itself was loaded from the
 * lab LAN. The token-bearing entry lives at the same-origin LAN path `/harness`,
 * which resolves through the ingress launcher and injects the harness launch
 * token at request time; no token is stored in the repository, the app registry
 * or the desktop preferences.
 */
export function HarnessLaunch() {
  return <section className="harness-launch" aria-label="DeepSeek Harness">
    <h2>DeepSeek Harness</h2>
    <p>XeonoLab 开发维护终端的快捷入口。仅在访问者与服务器处于同一内网时提供；入口由内网入口自动携带一次性 token，本机不保存 token。</p>
    <p className="muted">公网桌面不会显示此快捷入口。Harness 服务只监听 VM111 的局域网地址，并通过局域网防火墙限制为内网访问。</p>
    <div className="ds-button-row">
      <Button variant="primary" onClick={() => window.open('/harness', '_blank', 'noopener')}>打开 DeepSeek Harness</Button>
    </div>
  </section>;
}
