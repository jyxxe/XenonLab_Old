export class APIRequestError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'APIRequestError';
    this.status = status;
    this.code = code;
  }
}

export function isUnregisteredAccountError(error) {
  return error instanceof APIRequestError && error.status === 404 && error.code === 'ACCOUNT_NOT_FOUND';
}
