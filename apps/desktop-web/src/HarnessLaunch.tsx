import React from 'react';
import {Button} from './design-system/primitives';

/**
 * Launch surface for the DeepSeek Harness maintenance UI.
 *
 * The entry is available from the LAN desktop and from the public desktop. The
 * Harness UI and its launcher remain bound to the lab intranet (VM111, LAN-only
 * firewall) and are not exposed through the Cloudflare tunnel, so the shortcut
 * can complete only when the browser itself is on the same intranet — a remote
 * public visitor sees the entry but the LAN-only redirect cannot be reached.
 * No token is stored in the repository, the app registry or desktop
 * preferences; the LAN ingress injects it at request time.
 */
export function HarnessLaunch() {
  return <section className="harness-launch" aria-label="DeepSeek Harness">
    <h2>DeepSeek Harness</h2>
    <p>XeonoLab 开发维护终端的快捷入口。打开后由内网入口自动携带一次性 token，本机不保存 token。</p>
    <p className="muted">Harness 只监听 VM111 的局域网地址且未通过 Cloudflare Tunnel 暴露；因此只有与服务器处于同一内网的浏览器才能完成跳转，公网远程访问会在最后一步失败，不会暴露 Harness 界面。</p>
    <div className="ds-button-row">
      <Button variant="primary" onClick={() => window.open('/harness', '_blank', 'noopener')}>打开 DeepSeek Harness</Button>
    </div>
  </section>;
}
