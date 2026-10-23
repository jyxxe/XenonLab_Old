import {Icon} from '../design-system/Icon';
import React, {useMemo, useState} from 'react';
import {Badge, Button, IconButton, Input, Spinner} from '../design-system/primitives';
import {ancestorPaths, buildPathTree, filterTree, filteredTreeExpansion, flattenTree, recentEntryPresentation, recentListDisplay} from './logic.js';
import type {RecentEntry, Table} from './types';

export type LibraryViewKind = 'welcome' | 'library' | 'search' | 'query' | 'theorems';

export function LibrarySidebar({
  tables,
  loading,
  loadingMore,
  error,
  cursor,
  view,
  tableId,
  expanded,
  onToggleExpand,
  onSetView,
  onOpenTable,
  onLoadMore,
  onRetry,
  onCreateTable,
  recent,
  favorites,
  onOpenEntry,
  onToggleFavorite,
  theoremCount,
}: {
  tables: Table[];
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  cursor: string | null;
  view: LibraryViewKind;
  tableId: string | null;
  expanded: Set<string>;
  onToggleExpand: (path: string) => void;
  onSetView: (view: LibraryViewKind) => void;
  onOpenTable: (table: Table, expandPath?: boolean) => void;
  onLoadMore: () => void;
  onRetry: () => void;
  onCreateTable: () => void;
  recent: RecentEntry[];
  favorites: RecentEntry[];
  onOpenEntry: (entry: RecentEntry) => void;
  onToggleFavorite: (entry: RecentEntry) => void;
  theoremCount: number | null;
}) {
  const [filter, setFilter] = useState('');
  const [filterCollapsed, setFilterCollapsed] = useState<Set<string>>(new Set());
  const filtering = Boolean(filter.trim());
  const [showRecent, setShowRecent] = useState(true);
  const [showAllRecent, setShowAllRecent] = useState(false);
  const tree = useMemo(() => buildPathTree(tables), [tables]);
  const filteredTree = useMemo(() => filterTree(tree, filter), [tree, filter]);
  const effectiveExpanded = useMemo(() => filtering ? filteredTreeExpansion(filteredTree, filterCollapsed) : expanded, [filtering, filteredTree, filterCollapsed, expanded]);
  const visible = useMemo(() => filteredTree ? flattenTree(filteredTree, effectiveExpanded) : [], [filteredTree, effectiveExpanded]);

  function togglePath(path: string) {
    if (!filtering) return onToggleExpand(path);
    setFilterCollapsed(current => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  return (
    <aside className="knowledge-sidebar" aria-label="知识库导航">
      <div className="library-sidebar-head">
        <h2>知识库</h2>
        <IconButton label="刷新知识表目录" onClick={onRetry} disabled={loading}>⟳</IconButton>
      </div>

      <nav className="library-nav" aria-label="知识库视图">
        {[
          ['search', '搜索', '⌕'],
          ['query', '查询控制台', '{ }'],
          ['theorems', '定理', '⊢'],
        ].map(([id, label, icon]) => (
          <button
            key={id}
            type="button"
            className={view === id ? 'active' : ''}
            aria-pressed={view === id}
            onClick={() => onSetView(id as LibraryViewKind)}
          >
            <span aria-hidden="true"><Icon value={icon} size={18} /></span>
            <span>{label}</span>
            {id === 'theorems' && theoremCount != null ? <Badge>{theoremCount}</Badge> : null}
          </button>
        ))}
      </nav>

      <div className="library-sidebar-section">
        <div className="library-sidebar-section-head">
          <strong>收藏</strong>
          <small>{favorites.length}</small>
        </div>
        {favorites.length ? (
          <ul className="library-mini-list">
            {favorites.map(entry => (
              <li key={`${entry.kind}:${entry.id}`}>
                <button type="button" onClick={() => onOpenEntry(entry)} title={entry.title}>
                  <Icon value="favorite" size={16} /> {entry.title}
                </button>
                <IconButton label={`取消收藏 ${entry.title}`} size="sm" onClick={() => onToggleFavorite(entry)}>✕</IconButton>
              </li>
            ))}
          </ul>
        ) : (
          <p className="library-sidebar-empty">还没有收藏。在表或行上使用右键菜单即可添加。</p>
        )}
      </div>

      <div className="library-sidebar-section">
        <div className="library-sidebar-section-head">
          <strong>最近</strong>
          <small>{recent.length}</small>
          <button type="button" className="library-link" onClick={() => setShowRecent(value => !value)}>
            {showRecent ? '收起' : '展开'}
          </button>
        </div>
        {showRecent &&
          (recent.length ? (() => {
            const display = recentListDisplay(recent, showAllRecent);
            return <>
            <ul className="library-mini-list">
              {display.visible.map(entry => {
                const {title, identifier, label} = recentEntryPresentation(entry, recent);
                return (
                  <li key={`${entry.kind}:${entry.id}`}>
                    <button type="button" className={entry.kind === 'theorem' ? 'library-recent-theorem' : undefined} onClick={() => onOpenEntry(entry)} title={label}>
                      <span className="library-recent-title"><Icon value={entry.kind === 'table' ? 'library' : entry.kind === 'row' ? 'rows' : entry.kind === 'theorem' ? 'theorem' : 'home'} size={16} /> {title}</span>
                      {identifier && <span className="library-recent-identifier"> · {identifier}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
            {display.toggle && <button type="button" className="library-link library-recent-toggle" aria-expanded={display.expanded} onClick={() => setShowAllRecent(value => !value)}>{display.label}</button>}
            </>;
          })() : (
            <p className="library-sidebar-empty">还没有打开过任何对象。</p>
          ))}
      </div>

      <div className="library-sidebar-section library-tree-section">
        <div className="library-sidebar-section-head">
          <strong>路径</strong>
          <Button size="sm" variant="ghost" onClick={onCreateTable} title="新建知识表" aria-label="新建知识表"><Icon value="plus" size={16} /></Button>
        </div>
        <Input
          type="search"
          aria-label="筛选知识表"
          placeholder="搜索路径或表名"
          value={filter}
          onChange={event => { setFilter(event.target.value); setFilterCollapsed(new Set()); }}
        />
        {loading && !tables.length ? (
          <p className="library-sidebar-empty" role="status">
            <Spinner size="sm" label="正在加载知识表" /> 正在加载知识表…
          </p>
        ) : error ? (
          <p className="library-sidebar-empty library-error" role="alert">
            无法读取知识表目录。
            <button type="button" className="library-link" onClick={onRetry}>重试</button>
          </p>
        ) : !tables.length ? (
          <p className="library-sidebar-empty">这里还没有知识表。新建一张表，或把已有的表移动到该路径。</p>
        ) : !visible.length ? (
          <p className="library-sidebar-empty">没有匹配「{filter}」的路径或表。</p>
        ) : (
          <ul className="library-tree" role="tree">
            {visible.map(item => {
              if (item.kind === 'node') {
                const isOpen = effectiveExpanded.has(item.path);
                return (
                  <li key={`node:${item.path}`} role="treeitem" aria-expanded={isOpen}>
                    <button
                      type="button"
                      disabled={loading}
                      className="library-tree-node"
                      style={{paddingLeft: 8 + item.depth * 14}}
                      onClick={() => togglePath(item.path)}
                    >
                      <span aria-hidden="true"><Icon value={isOpen ? 'chevron-down' : 'chevron-right'} size={14} /></span>
                      <span>{item.path === '/' ? '全部' : item.name}</span>
                      <small>{item.count}</small>
                    </button>
                  </li>
                );
              }
              const table = item.table!;
              const active = view === 'library' && table.id === tableId;
              return (
                <li key={`table:${table.id}`} role="treeitem">
                  <button
                    type="button"
                    className={`library-tree-table${active ? ' active' : ''}`}
                    style={{paddingLeft: 8 + item.depth * 14}}
                    onClick={() => onOpenTable(table, !filtering)}
                    onContextMenu={event => {
                      event.preventDefault();
                      onToggleFavorite({kind: 'table', id: table.id, title: table.name, at: Date.now()});
                    }}
                    title={`${table.path}/${table.name}（右键收藏）`}
                  >
                    <strong>{table.name}</strong>
                    <small>{table.path}</small>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {cursor && (
          <Button size="sm" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? '加载中…' : '加载更多知识表'}
          </Button>
        )}
      </div>
    </aside>
  );
}

export function expandAncestors(expanded: Set<string>, path: string): Set<string> {
  const next = new Set(expanded);
  for (const ancestor of ancestorPaths(path)) next.add(ancestor);
  return next;
}
