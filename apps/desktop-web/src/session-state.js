export const initialSessionState = Object.freeze({phase: 'restoring', message: ''});

export function transitionSession(state, event) {
  switch (event.type) {
    case 'restore-started':
      return {...state, phase: 'restoring', message: ''};
    case 'restored':
      return {...state, phase: 'connected', message: ''};
    case 'no-session':
      return {...state, phase: 'signed-out', message: ''};
    case 'temporary-failure':
      return {...state, phase: 'retrying', message: event.message || '登录服务暂时不可用'};
    case 'reauthorization-required':
      return {...state, phase: 'reauthorize', message: event.message || '需要重新授权'};
    case 'user-logout':
      return {...state, phase: 'signed-out', message: '', token: '', persistent: false, windows: []};
    default:
      return state;
  }
}
