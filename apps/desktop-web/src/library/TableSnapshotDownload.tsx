import React, {useEffect, useRef, useState} from 'react';
import {Button, ErrorState} from '../design-system/primitives';
import {apiErrorFromResponse} from '../api-error.js';
import type {APIRaw} from '../shell/types';
import {describeError} from './logic.js';

/** Download the existing whole-table file, independent of the visible row view. */
export function TableSnapshotDownload({tableId, apiRaw}: {tableId: string; apiRaw: APIRaw}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [started, setStarted] = useState(false);
  const pending = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    ++generation.current;
    pending.current = false;
    setBusy(false); setError(null); setStarted(false);
    return () => {++generation.current;};
  }, [apiRaw]);

  async function download() {
    if (pending.current) return;
    pending.current = true;
    const owner = generation.current;
    setBusy(true); setError(null); setStarted(false);
    try {
      const response = await apiRaw(`/tables/${tableId}/export`);
      if (owner !== generation.current) return;
      if (!response.ok) throw apiErrorFromResponse(response, await response.json().catch(() => null));
      const blob = await response.blob();
      if (owner !== generation.current) return;
      // The current endpoint supplies an ASCII UUID.xl attachment filename.
      const filename = response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/i)?.[1] || `${tableId}.xl`;
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl; link.download = filename; link.rel = 'noopener';
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      setStarted(true);
    } catch (cause) {
      if (owner === generation.current) setError(cause);
    } finally {
      if (owner === generation.current) {pending.current = false; setBusy(false);}
    }
  }
  const info = error ? describeError(error, '下载 .xl 快照') : null;
  return <section aria-label="知识表快照">
    <Button size="sm" disabled={busy} onClick={() => void download()}>{busy ? '正在下载 .xl 快照…' : '下载 .xl 快照'}</Button>
    <p className="muted">全表 .xl 数据文件（含文件内元数据），不受筛选、选区或已加载分页限制；不含附件文件。</p>
    {started && <p role="status">已发起浏览器下载，请查看浏览器下载记录。</p>}
    {info && <ErrorState title={info.title} message={info.message} errorCode={info.code} requestId={info.requestId} onRetry={() => void download()} />}
  </section>;
}
