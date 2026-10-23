import {APIRequestError} from './api-error.js';

/**
 * Create-account form rules mirrored from `POST /api/v1/auth/accounts`.
 * The server enforces these limits; this module only produces visible,
 * actionable Chinese feedback before a request is attempted.
 */
export const USERNAME_MIN_LENGTH = 3;
export const USERNAME_MAX_LENGTH = 64;
export const USERNAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$/;
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 256;
export const USERNAME_HINT = '3–64 位，以字母或数字开头，可含 . _ -';
export const PASSWORD_HINT = '至少 12 个字符';

const DUPLICATE_USERNAME_MESSAGE = '登录名已存在，请换一个登录名';
const SERVER_VALIDATION_MESSAGE = '提交内容未通过服务器校验，请检查后重试';
const INTERACTIVE_LOGIN_MESSAGE = '请通过统一登录交互登录后再创建子账号';
const CREATE_ACCOUNT_FALLBACK = '创建子账号失败，请重试';

function text(value) {
  return typeof value === 'string' ? value : '';
}

/** Username: 3–64 chars, starts alphanumeric, then letters/digits/./_/- only. */
export function validateUsername(value) {
  const username = text(value);
  if (!username) return '请填写登录名';
  if (username.length < USERNAME_MIN_LENGTH) return `登录名至少需要 ${USERNAME_MIN_LENGTH} 个字符`;
  if (username.length > USERNAME_MAX_LENGTH) return `登录名最多 ${USERNAME_MAX_LENGTH} 个字符`;
  if (!/^[a-zA-Z0-9]/.test(username)) return '登录名必须以字母或数字开头';
  if (!USERNAME_PATTERN.test(username)) return '登录名只能包含字母、数字、点、横线或下划线';
  return '';
}

export function validateDisplayName(value) {
  return text(value).trim() ? '' : '请填写显示名称';
}

/** Initial password: 12–256 characters, matching the server boundary. */
export function validateInitialPassword(value) {
  const password = text(value);
  if (!password) return '请填写初始密码';
  if (password.length < PASSWORD_MIN_LENGTH) return `初始密码至少需要 ${PASSWORD_MIN_LENGTH} 个字符`;
  if (password.length > PASSWORD_MAX_LENGTH) return `初始密码最多 ${PASSWORD_MAX_LENGTH} 个字符`;
  return '';
}

/**
 * Validate every create-account field. Callers show `errors` next to each
 * input and focus `firstInvalidField`; an invalid form must not send a POST.
 */
export function validateNewAccount(fields) {
  const errors = {};
  const usernameError = validateUsername(fields?.username);
  if (usernameError) errors.username = usernameError;
  const nameError = validateDisplayName(fields?.name);
  if (nameError) errors.name = nameError;
  const passwordError = validateInitialPassword(fields?.password);
  if (passwordError) errors.password = passwordError;
  const order = ['username', 'name', 'password'];
  return {
    valid: order.every(field => !errors[field]),
    errors,
    firstInvalidField: order.find(field => errors[field]) || null,
  };
}

/**
 * Turn a failed `POST /auth/accounts` into a message shown next to the form.
 * 409/duplicate, 422 validation, and 403 interactive-login are the documented
 * create-account failures; other errors keep their server text when present.
 */
export function formatCreateAccountError(error) {
  const message = text(error?.message).trim();
  if (!(error instanceof APIRequestError)) return message || CREATE_ACCOUNT_FALLBACK;
  if (error.code === 'USERNAME_EXISTS' || error.status === 409) return DUPLICATE_USERNAME_MESSAGE;
  if (error.status === 422) return message || SERVER_VALIDATION_MESSAGE;
  if (error.status === 403 && error.code === 'INTERACTIVE_LOGIN_REQUIRED') return INTERACTIVE_LOGIN_MESSAGE;
  if (error.status === 403) return message || '当前登录方式无法创建子账号';
  if (error.status === 0) return message || '网络异常，请检查连接后重试';
  return message || CREATE_ACCOUNT_FALLBACK;
}
