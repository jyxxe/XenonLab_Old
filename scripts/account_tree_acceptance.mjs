export function isExpectedUnregisteredAccountTreeResponse(method, pathname, status, payload) {
  return method === 'GET' && pathname === '/api/v1/auth/accounts' && status === 404 && payload?.error?.code === 'ACCOUNT_NOT_FOUND';
}

export function mayInitializeAccountTree(identity) {
  return identity?.kind === 'human' && Array.isArray(identity.scopes) && identity.scopes.includes('desktop:admin');
}
