type ACLAccount = { id: string };

export function mergeManagedGrants(
  existing: Record<string, string[]>,
  draft: Record<string, string[]>,
  accounts: ACLAccount[],
): Record<string, string[]>;
