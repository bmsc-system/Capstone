import { useState } from 'react';
import { Button, Input, Popover } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import './OperationInstructions.css';

export const INSTRUCTIONS_MAX = 1000;

type Props = {
  notes?: string | null;
  /** Without it the line is read-only, and hidden when there is nothing to show. */
  onSave?: (text: string) => void | Promise<void>;
  className?: string;
};

/**
 * An operation's instructions for the worker: a quiet line at the foot of the
 * operation. Click it (or Edit) to change them.
 */
export default function OperationInstructions({ notes, onSave, className }: Props) {
  const text = notes?.trim() || '';
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(text);
  const [saving, setSaving] = useState(false);
  const editable = Boolean(onSave);

  if (!text && !editable) return null;

  const openEditor = (next: boolean) => {
    if (next) setDraft(text);
    setOpen(next);
  };

  const save = async () => {
    if (!onSave) return;
    setSaving(true);
    try {
      await onSave(draft.trim());
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const line = (
    <div
      className={`op-instr${text ? '' : ' op-instr--empty'}${editable ? ' is-editable' : ''}${className ? ` ${className}` : ''}`}
      role={editable ? 'button' : undefined}
      tabIndex={editable ? 0 : undefined}
      aria-label={editable ? (text ? 'Edit instructions' : 'Add instructions') : undefined}
      onKeyDown={(e) => {
        if (editable && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          openEditor(true);
        }
      }}
    >
      <FileTextOutlined className="op-instr__icon" aria-hidden />
      <span className="op-instr__text">{text || 'Add instructions for the worker (optional)'}</span>
      {editable && text ? <span className="op-instr__edit">Edit</span> : null}
    </div>
  );

  if (!editable) return line;

  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      open={open}
      onOpenChange={openEditor}
      title="Instructions for the worker"
      destroyOnHidden
      content={
        <div className="op-instr__pop">
          <Input.TextArea
            autoFocus
            autoSize={{ minRows: 3, maxRows: 8 }}
            maxLength={INSTRUCTIONS_MAX}
            showCount
            placeholder="Tolerances, setup, special handling"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="op-instr__actions">
            <Button size="small" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="small" type="primary" loading={saving} onClick={() => void save()}>
              Save
            </Button>
          </div>
        </div>
      }
    >
      {line}
    </Popover>
  );
}
