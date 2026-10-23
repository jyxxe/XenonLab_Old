import type {Tone} from './design-system/primitives';

export const QUEUE_STATUS_LABELS: Record<string, string>;
export const QUEUE_STATUS_TONES: Record<string, Tone>;

export function queueStatusLabel(status: string | null | undefined): string;
export function queueStatusTone(status: string | null | undefined): Tone;
export function formatQueueDuration(ms: number | null | undefined): string;
export function formatQueueTime(seconds: number | string | null | undefined): string;
export function appendUnique<T extends {id: number | string}>(existing: T[], incoming: T[]): T[];
export function summarizeQueue(counts: Record<string, number> | null | undefined): {
  running: number;
  completed: number;
  failed: number;
};
