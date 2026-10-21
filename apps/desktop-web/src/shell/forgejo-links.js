/** Shared Forgejo origin selection used by desktop navigation and Issue links. */
export function forgejoBase(hostname = globalThis.location?.hostname) {
  return hostname === 'xenon.jyxxe.com' ? 'https://xenon.jyxxe.com/dev' : 'https://192.168.0.101:9445';
}

export function forgejoIssueUrl(repository, issueId, hostname = globalThis.location?.hostname) {
  if (typeof repository !== 'string' || !Number.isSafeInteger(issueId) || issueId <= 0) return null;
  const [owner, repo, ...rest] = repository.split('/');
  if (rest.length || !owner || !repo || ['.', '..'].includes(owner) || ['.', '..'].includes(repo)) return null;
  if (!/^[-A-Za-z0-9_.]+$/.test(owner) || !/^[-A-Za-z0-9_.]+$/.test(repo)) return null;
  return `${forgejoBase(hostname)}/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues/${issueId}`;
}
