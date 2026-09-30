export class APIRequestError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string);
}

export function isUnregisteredAccountError(error: unknown): boolean;
