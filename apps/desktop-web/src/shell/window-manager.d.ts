import type {Rect, WindowState} from './types';

export const MENUBAR_HEIGHT: number;
export const DOCK_CLEARANCE: number;
export const WINDOW_MARGIN: number;
export const MIN_WIDTH: number;
export const MIN_HEIGHT: number;
export const MIN_VISIBLE_X: number;
export const MIN_VISIBLE_Y: number;

export function workArea(viewport: {width: number; height: number}): Rect;
export function clamp(value: number, min: number, max: number): number;
export function minSizeOf(app: {minSize?: [number, number]; min_size?: [number, number]} | null | undefined): {width: number; height: number};
export function clampRect(rect: Rect, viewport: {width: number; height: number}, minSize?: {width: number; height: number}): Rect;
export function defaultRect(
  app: {defaultSize?: [number, number]; default_size?: [number, number]; minSize?: [number, number]; min_size?: [number, number]} | null | undefined,
  viewport: {width: number; height: number},
  index?: number,
): Rect;
export function resizeRect(
  rect: Rect,
  minSize: {width: number; height: number},
  handle: string,
  dx: number,
  dy: number,
  viewport: {width: number; height: number},
): Rect;
export function moveRect(rect: Rect, x: number, y: number, viewport: {width: number; height: number}): Rect;
export function toggleMaximize(win: WindowState, viewport: {width: number; height: number}): WindowState;
export function focusWindow(windows: WindowState[], id: string): WindowState[];
export function activeWindow(windows: WindowState[]): WindowState | null;
export function nextWindowId(windows: WindowState[], activeId: string | null, direction?: number): string | null;
export function restoreWindows(
  raw: unknown,
  knownApp: (id: string) => {title: string; defaultSize?: [number, number]} | null | undefined,
  viewport: {width: number; height: number},
): WindowState[];
export function serializeWindows(windows: WindowState[]): Array<Record<string, unknown>>;
