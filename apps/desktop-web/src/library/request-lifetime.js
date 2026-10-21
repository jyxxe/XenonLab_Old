import {APIRequestError} from '../api-error.js';

/** A workspace owns its requests, including any continuation after an await. */
export function createRequestLifetime() {
  let active = false;
  let generation = 0;
  const ended = () => new APIRequestError('知识库会话已更改，请重新打开资源。', 0, 'SESSION_CHANGED');
  return {
    activate() { active = true; },
    end() { active = false; generation += 1; },
    bind(request) {
      return async (...args) => {
        if (!active) throw ended();
        const current = generation;
        try {
          const result = await request(...args);
          if (!active || generation !== current) throw ended();
          return result;
        } catch (error) {
          if (!active || generation !== current) throw ended();
          throw error;
        }
      };
    },
  };
}
