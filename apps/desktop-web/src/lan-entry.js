/**
 * Explicit intranet entry for the Xenon Panel (issue #293).
 *
 * A browser cannot silently switch from the public origin to the LAN origin:
 * the LAN edge uses Caddy's internal CA, cross-origin calls need CORS and
 * credentials, and Cloudflare Tunnel traffic reaches the origin from the tunnel
 * connector, so the server cannot see the client's network position. The
 * practical option is a visible, copyable LAN entry that the user (or an MDM
 * profile that installed the LAN CA) can open deliberately.
 */
export const DEFAULT_LAN_ORIGIN = 'https://192.168.0.101:9443';

/** Normalise the configured LAN origin, falling back to the documented default. */
export function lanEntryOrigin(configured) {
  const value = typeof configured === 'string' && configured.trim() ? configured.trim() : DEFAULT_LAN_ORIGIN;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.origin : DEFAULT_LAN_ORIGIN;
  } catch {
    return DEFAULT_LAN_ORIGIN;
  }
}

/**
 * Describe the LAN entry for the current page.
 *
 * Returns the target origin, whether the page is already on it, and a hint that
 * states the CA/issuer caveats instead of implying a silent speed switch.
 */
export function lanEntryState(currentOrigin, configured) {
  const origin = lanEntryOrigin(configured);
  const current = typeof currentOrigin === 'string' ? currentOrigin.trim() : '';
  const onLan = current !== '' && current === origin;
  return {
    origin,
    current: onLan,
    hint: onLan
      ? '当前已通过内网 IP 入口访问；公网入口仍可用于外网或未信任 LAN CA 的设备。'
      : '内网设备可直连此地址以减少公网往返；需先信任 LAN CA。内网与公网使用不同的 OIDC issuer，切换入口后可能需要重新登录。',
  };
}

/** Best-effort copy; returns whether the clipboard accepted the value. */
export async function copyLanEntry(origin, clipboard = globalThis.navigator?.clipboard) {
  if (!clipboard || typeof clipboard.writeText !== 'function') return false;
  try {
    await clipboard.writeText(origin);
    return true;
  } catch {
    return false;
  }
}
