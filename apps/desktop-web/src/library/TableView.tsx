import {Icon} from '../design-system/Icon';
import {stringifyExactJSON} from './json.js';
import React, {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {Badge, Button, Checkbox, ContextMenu, EmptyState, ErrorState, Input, Skeleton, StatusDot, type MenuItem} from '../design-system/primitives';
import {clampColumnWidth, keyboardColumnWidth, describeError, filterRows, formatNumber, relativeTime, rowHasAttachment, sortRows, virtualWindow} from './logic.js';
import type {Column, Row, Table} from './types';
import {serializeTSV} from './tsv.js';

const ROW_HEIGHT = 34;
const SYSTEM_COLUMNS: Array<{name: string; label: string}> = [
  {name: '_row_id', label: 'Row ID'},
  {name: '_updated_at', label: '更新时间'},
];

export type SortState = {key: string; direction: 'asc' | 'desc'} | null;

function cellText(row: Row, column: Column): string {
  const value = row.values?.[column.name];
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return stringifyExactJSON(value);
  return String(value);
}

export function TableView({
  table,
  columns,
  rows,
  cursor,
  loading,
  loadingMore,
  error,
  filter,
  onFilter,
  sort,
  onSort,
  selection,
  activeRowId,
  onSelectRow,
  onActivateRow,
  onSelectionChange,
  onLoadMore,
  onRetry,
  onRefresh,
  onContextMenu,
  onAskAI,
  canWrite,
}: {
  table: Table;
  columns: Column[];
  rows: Row[];
  cursor: string | null;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  filter: string;
  onFilter: (value: string) => void;
  sort: SortState;
  onSort: (key: string) => void;
  selection: Set<string>;
  activeRowId: string | null;
  onSelectRow: (rowId: string, event: React.MouseEvent, visibleRowIds: string[]) => void;
  onActivateRow: (rowId: string) => void;
  onSelectionChange: (next: Set<string>) => void;
  onLoadMore: () => void;
  onRetry: () => void;
  onRefresh: () => void;
  onContextMenu: (row: Row, x: number, y: number) => void;
  onAskAI: (rows: Row[]) => void;
  canWrite: boolean;
}) {
  const [copyBusy, setCopyBusy] = useState(false);
  const [copyMessage, setCopyMessage] = useState<{error: boolean; text: string} | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewportHeight, setViewportHeight] = useState(400);
  const [scrollTop, setScrollTop] = useState(0);
  const [widths, setWidths] = useState<Record<string, number>>({});
  const [measuredWidths, setMeasuredWidths] = useState<Record<string, number>>({});
  const widthOwner = useRef(table.id);
  widthOwner.current = table.id;
  const stopResize = useRef<(() => void) | null>(null);
  const [columnMenu, setColumnMenu] = useState<{row: Row; x: number; y: number} | null>(null);

  const gridColumns = useMemo(() => [...SYSTEM_COLUMNS.map(column => ({...column, schema: null as Column | null})), ...columns.map(column => ({name: column.name, label: column.name, schema: column}))], [columns]);

  const visibleRows = useMemo(() => {
    const filtered = filterRows(rows, filter);
    return sort ? sortRows(filtered, sort.key, sort.direction) : filtered;
  }, [rows, filter, sort]);
  const visibleRowIds = useMemo(() => visibleRows.map(row => row._row_id), [visibleRows]);

  const selectedVisibleRows = visibleRows.filter(row => selection.has(row._row_id));

  useEffect(() => setCopyMessage(null), [table.id, selection, filter, sort]);

  async function copySelected() {
    if (!selectedVisibleRows.length || copyBusy) return;
    const selected = selectedVisibleRows;
    setCopyBusy(true); setCopyMessage(null);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('当前浏览器无法写入剪贴板。');
      const cells = selected.map(row => [
        ...gridColumns.map(column => column.schema ? row.values?.[column.name] : row[column.name as keyof Row]),
        [row._deleted_at ? '已删除' : '', rowHasAttachment(row, columns) ? '附件' : '', row._revision > 1 ? `r${row._revision}` : ''].filter(Boolean).join(' '),
      ]);
      await navigator.clipboard.writeText(serializeTSV([...gridColumns.map(column => column.label), '状态'], cells));
      setCopyMessage({error: false, text: `已复制 ${selected.length} 行为 TSV。`});
    } catch {
      setCopyMessage({error: true, text: '复制失败，请检查浏览器剪贴板权限后重试；选择已保留。'});
    } finally { setCopyBusy(false); }
  }

  const windowed = virtualWindow(scrollTop, viewportHeight, visibleRows.length, ROW_HEIGHT);
  const slice = visibleRows.slice(windowed.start, windowed.end);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const update = () => setViewportHeight(element.clientHeight || 400);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setScrollTop(0);
    scrollRef.current?.scrollTo({top: 0});
  }, [table.id, filter, sort?.key, sort?.direction]);

  // Opening a row from search, recents or the inspector scrolls it into view so the
  // active row is always rendered, even when it sits outside the current window.
  useEffect(() => {
    if (!activeRowId) return;
    const index = visibleRows.findIndex(row => row._row_id === activeRowId);
    if (index < 0) return;
    const element = scrollRef.current;
    if (!element) return;
    const target = index * ROW_HEIGHT;
    if (target < element.scrollTop || target + ROW_HEIGHT > element.scrollTop + element.clientHeight) {
      element.scrollTo({top: Math.max(0, target - element.clientHeight / 2)});
    } else {
      setScrollTop(element.scrollTop);
    }
  }, [activeRowId, visibleRows]);

  // Column widths are a per-table view preference and survive reloads.
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(`xeonolab.library.columns.${table.id}`) || '{}');
      setWidths(stored && typeof stored === 'object' ? stored : {});
    } catch {
      setWidths({});
    }
  }, [table.id]);

  // Unset preferences start at the actual header size, rather than an arbitrary width.
  useLayoutEffect(() => {
    const grid = scrollRef.current;
    if (!grid) return;
    const measure = () => {
      const next: Record<string, number> = {};
      grid.querySelectorAll<HTMLTableCellElement>('th[data-resize-column]').forEach(header => {
        next[header.dataset.resizeColumn!] = clampColumnWidth(header.getBoundingClientRect().width);
      });
      setMeasuredWidths(current => Object.keys(next).length === Object.keys(current).length && Object.keys(next).every(key => next[key] === current[key]) ? current : next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    const element = grid.querySelector('table');
    if (element) observer.observe(element);
    return () => observer.disconnect();
  }, [table.id, gridColumns, widths, loading]);

  useEffect(() => () => { stopResize.current?.(); }, [table.id]);

  const setColumnWidth = useCallback((name: string, width: number) => {
    if (widthOwner.current !== table.id) return;
    setWidths(current => {
      const updated = {...current, [name]: clampColumnWidth(width)};
      try {
        localStorage.setItem(`xeonolab.library.columns.${table.id}`, stringifyExactJSON(updated));
      } catch {
        /* view preference only */
      }
      return updated;
    });
  }, [table.id]);

  const resizeColumn = useCallback((name: string, startWidth: number, startX: number) => {
    stopResize.current?.();
    const move = (event: PointerEvent) => setColumnWidth(name, startWidth + event.clientX - startX);
    const up = () => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      removeEventListener('pointercancel', up);
      stopResize.current = null;
    };
    stopResize.current = up;
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', up);
  }, [setColumnWidth]);

  const moveActive = (direction: number, extend: boolean) => {
    if (!visibleRows.length) return;
    const index = visibleRows.findIndex(row => row._row_id === activeRowId);
    const nextIndex = Math.max(0, Math.min(visibleRows.length - 1, (index < 0 ? (direction > 0 ? -1 : visibleRows.length) : index) + direction));
    const next = visibleRows[nextIndex];
    if (!next) return;
    onActivateRow(next._row_id);
    if (extend) {
      const nextSelection = new Set(selection);
      nextSelection.add(next._row_id);
      onSelectionChange(nextSelection);
    } else {
      onSelectionChange(new Set([next._row_id]));
    }
    const rowTop = nextIndex * ROW_HEIGHT;
    const element = scrollRef.current;
    if (element && (rowTop < element.scrollTop || rowTop + ROW_HEIGHT > element.scrollTop + element.clientHeight)) {
      element.scrollTo({top: Math.max(0, rowTop - element.clientHeight / 2)});
    }
  };

  const allSelected = visibleRows.length > 0 && visibleRows.every(row => selection.has(row._row_id));

  // The AI action targets the current selection; a context menu on an
  // unselected row upgrades it to that row first so the draft always matches
  // what the user is looking at.
  function rowsForAI(row: Row): Row[] {
    if (selection.has(row._row_id)) return selectedVisibleRows;
    return [row];
  }

  function menuItems(row: Row): MenuItem[] {
    return [
      {id: 'open', label: '在 Inspector 中打开', onSelect: () => onActivateRow(row._row_id)},
      {id: 'copy-id', label: '复制 Row ID', onSelect: () => void navigator.clipboard?.writeText(row._row_id)},
      {
        id: 'copy-row',
        label: '复制记录 JSON',
        onSelect: () => void navigator.clipboard?.writeText(stringifyExactJSON(row.values, 2)),
      },
      {separator: true},
      {
        id: 'ask-ai',
        label: rowsForAI(row).length > 1 ? `Ask AI about selection (${rowsForAI(row).length} 行)` : 'Ask AI about this row',
        onSelect: () => onAskAI(rowsForAI(row)),
      },
      {
        id: 'selection',
        label: selection.size > 1 ? `已选择 ${selection.size} 行` : '选择该行',
        onSelect: () => onSelectionChange(new Set([row._row_id])),
      },
    ];
  }

  if (error) {
    const info = describeError(error, '读取记录');
    return <ErrorState title={info.title} message={info.message} requestId={info.requestId} errorCode={info.code} onRetry={onRetry} />;
  }

  return (
    <div className="library-table-view">
      {loading && rows.length > 0 && <div className="library-loading-bar" role="status" aria-label="正在刷新记录" />}
      <div className="library-table-toolbar">
        <Input
          type="search"
          aria-label="筛选已加载记录"
          placeholder="筛选已加载记录（服务端过滤请使用查询控制台）"
          value={filter}
          onChange={event => onFilter(event.target.value)}
        />
        <span className="library-table-count">
          {formatNumber(visibleRows.length)} / {formatNumber(rows.length)} 行已加载
        </span>
        <Button size="sm" onClick={onRefresh} disabled={loading}>{loading ? '刷新中…' : '刷新记录'}</Button>
        {selection.size > 0 && (
          <>
            <Badge tone="accent">已选 {selection.size} 行</Badge>
            <Button size="sm" loading={copyBusy} disabled={!selectedVisibleRows.length} onClick={() => void copySelected()}>复制已选记录为 TSV（{selectedVisibleRows.length} 行）</Button>
            <Button size="sm" variant="ghost" onClick={() => onAskAI(selectedVisibleRows)}>Ask AI about selection</Button>
            <Button size="sm" variant="ghost" onClick={() => onSelectionChange(new Set())}>清除选择</Button>
          </>
        )}
      </div>

      {selection.size > 0 && <p className="muted library-copy-boundary">仅复制已加载且当前筛选可见的选中行，不加载后续分页。</p>}
      {copyMessage && <p className={copyMessage.error ? 'library-copy-error' : 'muted'} role={copyMessage.error ? 'alert' : 'status'}>{copyMessage.text}</p>}

      {loading && !rows.length ? (
        <div className="library-table-skeleton" role="status" aria-label="正在加载记录">
          {Array.from({length: 8}, (_, index) => <Skeleton key={index} height={ROW_HEIGHT - 8} />)}
        </div>
      ) : !rows.length ? (
        <div className="table-scroll library-table-empty">
          <p>没有可显示的记录。</p>
          <p className="muted">当前窗口没有可读记录；可能全部被 ACL 过滤，或这张表还没有写入数据。</p>
        </div>
      ) : !visibleRows.length ? (
        <EmptyState
          icon="▤"
          title="没有匹配的已加载记录"
          description="当前筛选只作用于已加载的行；完整过滤请使用查询控制台中的 DSL。"
        />
      ) : (
        <div
          className="table-scroll"
          ref={scrollRef}
          tabIndex={0}
          role="grid"
          aria-label={`${table.name} 数据表`}
          onScroll={event => setScrollTop(event.currentTarget.scrollTop)}
          onKeyDown={event => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              moveActive(1, event.shiftKey);
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              moveActive(-1, event.shiftKey);
            } else if (event.key === ' ' && activeRowId) {
              event.preventDefault();
              const next = new Set(selection);
              if (next.has(activeRowId)) next.delete(activeRowId);
              else next.add(activeRowId);
              onSelectionChange(next);
            } else if (event.key === 'Enter' && activeRowId) {
              event.preventDefault();
              onActivateRow(activeRowId);
            } else if (event.key === 'Escape') {
              onSelectionChange(new Set());
            } else if (event.key.toLowerCase() === 'a' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              onSelectionChange(new Set(visibleRows.map(row => row._row_id)));
            }
          }}
        >
          <table className="ds-table library-table">
            <thead>
              <tr>
                <th className="library-th-select">
                  <Checkbox
                    aria-label="选择全部已加载记录"
                    checked={allSelected}
                    onChange={event => onSelectionChange(event.target.checked ? new Set(visibleRows.map(row => row._row_id)) : new Set())}
                  />
                </th>
                {gridColumns.map(column => (
                  <th key={column.name} data-resize-column={column.name} style={{width: widths[column.name], minWidth: widths[column.name]}}>
                    <span className="library-th-inner">
                      <button
                        type="button"
                        className="library-sort"
                        onClick={() => onSort(column.name)}
                        title={`按 ${column.label} 排序`}
                      >
                        {column.label}
                        {sort?.key === column.name ? <span aria-hidden="true"><Icon value={sort.direction === 'asc' ? 'arrow-up' : 'arrow-down'} size={14} /></span> : null}
                      </button>
                      <span
                        className="library-th-resize"
                        role="separator"
                        tabIndex={0}
                        aria-orientation="vertical"
                        aria-valuemin={80}
                        aria-valuemax={520}
                        aria-valuenow={widths[column.name] ?? measuredWidths[column.name] ?? 160}
                        aria-valuetext={`${Math.round(widths[column.name] ?? measuredWidths[column.name] ?? 160)} 像素`}
                        title="左右方向键调整 10 像素；Shift 调整 40 像素；Home 最窄，End 最宽"
                        onKeyDown={event => {
                          if (event.key !== 'Tab') event.stopPropagation();
                          const next = keyboardColumnWidth(widths[column.name] ?? measuredWidths[column.name] ?? 160, event.key, event.shiftKey);
                          if (next === null) return;
                          event.preventDefault();
                          setColumnWidth(column.name, next);
                        }}
                        aria-label={`调整 ${column.label} 列宽`}
                        onPointerDown={event => {
                          event.preventDefault();
                          event.currentTarget.focus();
                          resizeColumn(column.name, widths[column.name] ?? measuredWidths[column.name] ?? 160, event.clientX);
                        }}
                      />
                    </span>
                  </th>
                ))}
                <th className="library-th-status">状态</th>
              </tr>
            </thead>
            <tbody>
              {windowed.padTop > 0 && (
                <tr aria-hidden="true" className="library-spacer">
                  <td colSpan={gridColumns.length + 2} style={{height: windowed.padTop, padding: 0, border: 0}} />
                </tr>
              )}
              {slice.map(row => {
                const selected = selection.has(row._row_id);
                const active = row._row_id === activeRowId;
                return (
                  <tr
                    key={row._row_id}
                    className={[selected ? 'active' : '', 'ds-selected', active ? 'is-active-row' : ''].filter(Boolean).join(' ')}
                    data-row-id={row._row_id}
                    aria-selected={selected}
                    style={{height: ROW_HEIGHT}}
                    onClick={event => onSelectRow(row._row_id, event, visibleRowIds)}
                    onDoubleClick={() => onActivateRow(row._row_id)}
                    onContextMenu={event => {
                      event.preventDefault();
                      setColumnMenu({row, x: event.clientX, y: event.clientY});
                    }}
                  >
                    <td className="library-td-select" onClick={event => event.stopPropagation()}>
                      <Checkbox
                        aria-label={`选择记录 ${row._row_id}`}
                        checked={selected}
                        onChange={event => {
                          const next = new Set(selection);
                          if (event.target.checked) next.add(row._row_id);
                          else next.delete(row._row_id);
                          onSelectionChange(next);
                        }}
                      />
                    </td>
                    {gridColumns.map(column => {
                      const schema = column.schema;
                      return (
                        <td key={column.name} title={schema ? cellText(row, schema) : String(row[column.name as keyof Row] ?? '')}>
                          {schema ? cellText(row, schema) : column.name === '_updated_at' ? relativeTime(row._updated_at) : String(row[column.name as keyof Row] ?? '')}
                        </td>
                      );
                    })}
                    <td className="library-td-status">
                      {row._deleted_at ? <Badge tone="danger">已删除</Badge> : null}
                      {rowHasAttachment(row, columns) ? <Badge tone="accent"><Icon value="attachment" size={14} /> 附件</Badge> : null}
                      {row._revision > 1 ? <Badge>r{row._revision}</Badge> : null}
                    </td>
                  </tr>
                );
              })}
              {windowed.padBottom > 0 && (
                <tr aria-hidden="true" className="library-spacer">
                  <td colSpan={gridColumns.length + 2} style={{height: windowed.padBottom, padding: 0, border: 0}} />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="library-table-footer">
        {cursor ? (
          <Button onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? '加载中…' : '加载更多记录'}</Button>
        ) : (
          <span className="muted">已到记录末尾</span>
        )}
        <span className="muted">
          <StatusDot tone={canWrite ? 'success' : 'neutral'} /> {canWrite ? '可写' : '只读'}
        </span>
      </div>

      {columnMenu && <ContextMenu x={columnMenu.x} y={columnMenu.y} items={menuItems(columnMenu.row)} onClose={() => setColumnMenu(null)} />}
    </div>
  );
}
