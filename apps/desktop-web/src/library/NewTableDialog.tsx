import React, {useState} from 'react';
import {Button, ErrorState, Input, Modal} from '../design-system/primitives';
import {initialColumns, SchemaEditor, type Column} from '../SchemaEditor';
import {describeError} from './logic.js';

export function NewTableDialog({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (payload: {path: string; name: string; columns: Column[]}) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [path, setPath] = useState('/知识');
  const [columns, setColumns] = useState<Column[]>(initialColumns.map(column => ({...column})));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const errorInfo = error ? describeError(error, '创建知识表') : null;
  const valid = name.trim().length > 0 && path.trim().startsWith('/') && columns.every(column => column.name.trim() && column.type);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onCreate({path: path.trim(), name: name.trim(), columns});
      setName('');
      setColumns(initialColumns.map(column => ({...column})));
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="新建知识表"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={!valid || busy} onClick={() => void submit()}>{busy ? '创建中…' : '创建知识表'}</Button>
        </>
      }
    >
      {errorInfo && <ErrorState title={errorInfo.title} message={errorInfo.message} requestId={errorInfo.requestId} errorCode={errorInfo.code} />}
      <p className="muted">创建后直接进入 Table View。`.xl` 表的实际字段由这里的 schema 决定；删除或改类型属于破坏性操作，保存 schema 时会单独确认。</p>
      <label>
        表名
        <Input value={name} onChange={event => setName(event.target.value)} placeholder="uniform-convergence" maxLength={128} />
      </label>
      <label>
        逻辑路径
        <Input value={path} onChange={event => setPath(event.target.value)} placeholder="/数学/分析" />
        <small className="muted">必须是绝对路径，例如 /数学/分析；路径权限不足时服务端会拒绝创建。</small>
      </label>
      <SchemaEditor columns={columns} change={setColumns} disabled={busy} />
    </Modal>
  );
}
