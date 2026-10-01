export type WorkStateItem = {status: string; claim_active?: boolean};

export function isActiveWork(item: WorkStateItem): boolean {
  return item.status === 'queued' || (item.status === 'claimed' && item.claim_active === true);
}

export function isExpiredWorkClaim(item: WorkStateItem): boolean {
  return item.status === 'claimed' && item.claim_active === false;
}

export function workDisplayStatus(item: WorkStateItem): string {
  return isExpiredWorkClaim(item) ? 'expired' : item.status;
}
