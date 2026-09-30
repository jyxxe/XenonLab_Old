export type SessionPhase = 'restoring' | 'connected' | 'signed-out' | 'retrying' | 'reauthorize';
export type SessionStatus = {phase: SessionPhase; message: string; [key: string]: unknown};
export type SessionEvent = {type: string; message?: string};
export const initialSessionState: SessionStatus;
export function transitionSession(state: SessionStatus, event: SessionEvent): SessionStatus;
