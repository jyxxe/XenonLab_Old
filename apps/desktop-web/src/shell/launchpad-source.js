/** Display only an external app's origin, never URL user-info or entry data. */
export function launchpadSource(app) {
  if (app.kind !== 'iframe' || !app.origin) return null;
  try {
    const url = new URL(app.origin);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return null;
    return {host: url.host, origin: url.origin};
  } catch {
    return null;
  }
}
