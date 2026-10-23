import React, {useEffect, useRef, useState} from 'react';
import {Badge, Button, Modal, Textarea} from '../design-system/primitives';
import {SELECTION_AI_ACTIONS, buildSelectionPrompt, selectionContextSummary} from './logic.js';
import type {SelectionAIAction} from './types';
import type {Column, Row, Table} from './types';

/**
 * Ask-AI extension point (Issue #71 §17). The draft is composed locally from
 * the current selection and stays fully editable; nothing is sent until the
 * user confirms. Delivery is routed through the shell's app-intent channel so
 * no knowledge about chat deployment (origin, auth) lives in XeLibrary.
 */
export function AskAIDialog({
  open,
  action,
  onAction,
  table,
  rows,
  columns,
  chatAvailable,
  onConfirm,
  onClose,
}: {
  open: boolean;
  action: SelectionAIAction;
  onAction: (action: SelectionAIAction) => void;
  table: Table;
  rows: Row[];
  columns: Column[];
  chatAvailable: boolean;
  onConfirm: (prompt: string) => void;
  onClose: () => void;
}) {
  const [question, setQuestion] = useState('');
  const [prompt, setPrompt] = useState('');
  const [copied, setCopied] = useState(false);
  const composedFor = useRef('');
  const summary = selectionContextSummary(table, rows, columns);

  // The draft tracks the selection only while it has not been edited yet, so
  // a background row refresh does not clobber what the user is reviewing.
  useEffect(() => {
    if (!open) return;
    const key = `${table.id}:${rows.map(row => row._row_id).join(',')}:${action}`;
    if (composedFor.current === key) return;
    composedFor.current = key;
    setPrompt(buildSelectionPrompt(action, table, rows, columns, question));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, table, rows, columns, action]);

  // The draft's question line is derived from the question box; updating the
  // question refreshes it in place without discarding other manual edits to
  // the template sections around it.
  useEffect(() => {
    if (!open || action !== 'ask') return;
    setPrompt(current => current.replace(/问题：.*$/m, `问题：${question.trim() || '（请在发送前补充你的问题）'}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question]);

  function regenerate(nextAction: SelectionAIAction) {
    onAction(nextAction);
    const key = `${table.id}:${rows.map(row => row._row_id).join(',')}:${nextAction}`;
    composedFor.current = key;
    setPrompt(buildSelectionPrompt(nextAction, table, rows, columns, question));
  }

  function confirm() {
    onConfirm(prompt.trim() ? prompt : buildSelectionPrompt(action, table, rows, columns, question));
  }

  return (
    <Modal
      open={open}
      title="Ask AI about selection"
      size="lg"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button
            onClick={async () => {
              try {
                await navigator.clipboard?.writeText(prompt);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              } catch {
                setCopied(false);
              }
            }}
          >
            {copied ? '已复制' : '复制草稿'}
          </Button>
          <Button variant="primary" disabled={!chatAvailable} onClick={confirm}>
            {chatAvailable ? '发送到 AI 对话' : 'AI 对话不可用'}
          </Button>
        </>
      }
    >
      <div className="library-ask-actions" role="group" aria-label="AI 操作">
        {SELECTION_AI_ACTIONS.map(entry => (
          <Button key={entry.id} size="sm" variant={entry.id === action ? 'primary' : 'default'} title={entry.hint} onClick={() => regenerate(entry.id)}>
            {entry.label}
          </Button>
        ))}
      </div>

      <p className="library-ask-meta muted">
        将与 AI 共享：<strong>{summary.tablePath}</strong> 的 {summary.rowCount} 行
        {summary.truncated ? <>（草稿只包含前 {summary.sharedRows} 行，其余被截断）</> : null}、
        字段 {summary.fields.join(', ') || '（无字段定义）'}。
        草稿完全可编辑；点击发送前不会发出任何请求。
      </p>

      {action === 'ask' && (
        <label className="library-ask-question">
          你的问题
          <Textarea
            aria-label="你的问题"
            placeholder="例如：这些记录之间存在什么共同模式？"
            value={question}
            onChange={event => setQuestion(event.target.value)}
          />
        </label>
      )}

      <label className="library-ask-draft">
        AI 草稿（可编辑）
        <Textarea
          aria-label="AI 草稿"
          className="library-ask-editor"
          spellCheck={false}
          value={prompt}
          onChange={event => setPrompt(event.target.value)}
        />
      </label>

      {!chatAvailable && (
        <p className="library-notice" role="status">
          当前没有可用的 AI 对话应用。OpenWebUI 部署并登记后，这里会直接投递草稿；现在可以复制草稿后自行粘贴使用。
        </p>
      )}
      {summary.truncated && <Badge tone="warning">选择超过 {summary.sharedRows} 行已截断</Badge>}
    </Modal>
  );
}
