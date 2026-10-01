export class APIRequestError extends Error {
  status: number;
  code?: string;
  requestId?: string;
  constructor(message: string, status: number, code?: string, metadata?: {requestId?: string});
}

export function isUnregisteredAccountError(error: unknown): boolean;

export function apiErrorFromResponse(response: Response, payload: unknown): APIRequestError;
