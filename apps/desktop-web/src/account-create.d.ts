export const USERNAME_MIN_LENGTH: number;
export const USERNAME_MAX_LENGTH: number;
export const USERNAME_PATTERN: RegExp;
export const PASSWORD_MIN_LENGTH: number;
export const PASSWORD_MAX_LENGTH: number;
export const USERNAME_HINT: string;
export const PASSWORD_HINT: string;

export type NewAccountFields = {username: string; name: string; password: string};
export type NewAccountField = keyof NewAccountFields;
export type NewAccountErrors = Partial<Record<NewAccountField, string>>;

export function validateUsername(value: string): string;
export function validateDisplayName(value: string): string;
export function validateInitialPassword(value: string): string;

export function validateNewAccount(fields: NewAccountFields): {
  valid: boolean;
  errors: NewAccountErrors;
  firstInvalidField: NewAccountField | null;
};

export function formatCreateAccountError(error: unknown): string;
