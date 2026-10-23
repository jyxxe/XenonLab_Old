import test from 'node:test';
import assert from 'node:assert/strict';
import {APIRequestError} from '../src/api-error.js';
import {
  PASSWORD_HINT,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  USERNAME_HINT,
  USERNAME_MAX_LENGTH,
  USERNAME_MIN_LENGTH,
  formatCreateAccountError,
  validateDisplayName,
  validateInitialPassword,
  validateNewAccount,
  validateUsername,
} from '../src/account-create.js';

test('username follows the server rule: 3-64 chars starting alphanumeric', () => {
  assert.equal(validateUsername('abc'), '');
  assert.equal(validateUsername('A1._-x'), '');
  assert.equal(validateUsername('a'.repeat(USERNAME_MIN_LENGTH)), '');
  assert.equal(validateUsername('a'.repeat(USERNAME_MAX_LENGTH)), '');
  assert.equal(validateUsername(''), '请填写登录名');
  assert.equal(validateUsername('ab'), '登录名至少需要 3 个字符');
  assert.equal(validateUsername('a'.repeat(USERNAME_MAX_LENGTH + 1)), '登录名最多 64 个字符');
  assert.equal(validateUsername('-abc'), '登录名必须以字母或数字开头');
  assert.equal(validateUsername('中文名'), '登录名必须以字母或数字开头');
  assert.equal(validateUsername('a b'), '登录名只能包含字母、数字、点、横线或下划线');
  assert.equal(validateUsername('ab@c'), '登录名只能包含字母、数字、点、横线或下划线');
  assert.equal(validateUsername(undefined), '请填写登录名');
});

test('display name is required but keeps its surrounding characters', () => {
  assert.equal(validateDisplayName('子账号'), '');
  assert.equal(validateDisplayName('a'), '');
  assert.equal(validateDisplayName(''), '请填写显示名称');
  assert.equal(validateDisplayName('   '), '请填写显示名称');
  assert.equal(validateDisplayName(undefined), '请填写显示名称');
});

test('initial password is 12-256 characters like the server boundary', () => {
  assert.equal(validateInitialPassword('x'.repeat(PASSWORD_MIN_LENGTH)), '');
  assert.equal(validateInitialPassword('x'.repeat(PASSWORD_MAX_LENGTH)), '');
  assert.equal(validateInitialPassword(''), '请填写初始密码');
  assert.equal(validateInitialPassword('x'.repeat(PASSWORD_MIN_LENGTH - 1)), '初始密码至少需要 12 个字符');
  assert.equal(validateInitialPassword('x'.repeat(PASSWORD_MAX_LENGTH + 1)), '初始密码最多 256 个字符');
  assert.equal(validateInitialPassword(undefined), '请填写初始密码');
});

test('validateNewAccount reports every field and focuses the first invalid one', () => {
  assert.deepEqual(validateNewAccount({username: '', name: '', password: ''}), {
    valid: false,
    errors: {username: '请填写登录名', name: '请填写显示名称', password: '请填写初始密码'},
    firstInvalidField: 'username',
  });
  const nameOnly = validateNewAccount({username: 'child.01', name: ' ', password: 'x'.repeat(12)});
  assert.equal(nameOnly.valid, false);
  assert.deepEqual(Object.keys(nameOnly.errors), ['name']);
  assert.equal(nameOnly.firstInvalidField, 'name');
  const passwordOnly = validateNewAccount({username: 'child.01', name: 'Child', password: 'short'});
  assert.equal(passwordOnly.valid, false);
  assert.deepEqual(Object.keys(passwordOnly.errors), ['password']);
  assert.equal(passwordOnly.firstInvalidField, 'password');
  assert.deepEqual(validateNewAccount({username: 'child.01', name: 'Child', password: 'x'.repeat(12)}), {
    valid: true,
    errors: {},
    firstInvalidField: null,
  });
});

test('create-account hints stay aligned with the documented limits', () => {
  assert.equal(USERNAME_HINT, '3–64 位，以字母或数字开头，可含 . _ -');
  assert.equal(PASSWORD_HINT, '至少 12 个字符');
});

test('server create-account errors map to actionable Chinese feedback', () => {
  assert.equal(
    formatCreateAccountError(new APIRequestError('username exists', 409, 'USERNAME_EXISTS')),
    '登录名已存在，请换一个登录名',
  );
  assert.equal(
    formatCreateAccountError(new APIRequestError('conflict', 409)),
    '登录名已存在，请换一个登录名',
  );
  assert.equal(
    formatCreateAccountError(new APIRequestError('password too short', 422, 'VALIDATION_ERROR')),
    'password too short',
  );
  assert.equal(
    formatCreateAccountError(new APIRequestError('', 422)),
    '提交内容未通过服务器校验，请检查后重试',
  );
  assert.equal(
    formatCreateAccountError(new APIRequestError('interactive login required', 403, 'INTERACTIVE_LOGIN_REQUIRED')),
    '请通过统一登录交互登录后再创建子账号',
  );
  assert.equal(
    formatCreateAccountError(new APIRequestError('forbidden', 403, 'PERMISSION_DENIED')),
    'forbidden',
  );
  assert.equal(formatCreateAccountError(new APIRequestError('boom', 500)), 'boom');
  assert.equal(formatCreateAccountError(new Error('network down')), 'network down');
  assert.equal(formatCreateAccountError(new Error('')), '创建子账号失败，请重试');
  assert.equal(formatCreateAccountError(undefined), '创建子账号失败，请重试');
});
