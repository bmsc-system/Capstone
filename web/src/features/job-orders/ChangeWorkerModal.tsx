import { useEffect, useState } from 'react';
import { Alert, Button, Modal, Radio, Spin, message } from 'antd';
import { getErrorMessage } from '../../api/client';
import { operationsApi } from '../../api/operations.api';
import type { Operation, OperationReplacements } from '../../types';
import { formatShop } from '../../utils/shopTime';

/**
 * Admin: give a released, unfinished operation to another worker. Work not
 * started keeps its booked time; work that was started and is paused is
 * resumed by the new worker. Only people who are free, working and skilled
 * for that time are offered.
 */
export default function ChangeWorkerModal({
  op,
  onClose,
  onChanged,
}: {
  op: Operation | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [options, setOptions] = useState<OperationReplacements | null>(null);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<string | undefined>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!op) return;
    setOptions(null);
    setError('');
    setPicked(undefined);
    operationsApi
      .replacements(op.id)
      .then(({ data }) => {
        setOptions(data);
        setPicked(data.candidates[0]?.workerId ?? data.helpers[0]?.workerId);
      })
      .catch((err) => setError(getErrorMessage(err)));
  }, [op]);

  const started = op ? op.status !== 'SCHEDULED' && op.status !== 'PENDING' : false;
  const people = options
    ? [
        ...options.helpers.map((h) => ({
          workerId: h.workerId,
          fullName: h.fullName ?? 'Helper',
          note: 'Already helping on this operation',
        })),
        ...options.candidates.map((c) => ({
          workerId: c.workerId,
          fullName: c.fullName,
          note: c.attendanceWarning || (c.proficiency ? `Skill level ${c.proficiency}` : 'Free at that time'),
        })),
      ]
    : [];

  const save = async () => {
    if (!op || !picked) return;
    setSaving(true);
    try {
      const { data } = await operationsApi.handOver(op.id, picked);
      message.success(`${data.label} is now with ${data.to}`);
      onChanged();
      onClose();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={Boolean(op)}
      onCancel={onClose}
      title={op ? `Change worker: ${op.operationName || 'Operation'}` : 'Change worker'}
      footer={[
        <Button key="cancel" onClick={onClose}>
          Cancel
        </Button>,
        <Button key="ok" type="primary" loading={saving} disabled={!picked} onClick={save}>
          {started ? 'Hand over' : 'Change worker'}
        </Button>,
      ]}
      destroyOnClose
    >
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {!error && !options ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      ) : null}
      {options ? (
        <>
          <div style={{ fontSize: 13, color: '#475569', marginBottom: 12 }}>
            {started
              ? 'This operation was started and is paused. The new worker resumes it; the time already worked is kept.'
              : `Booked ${formatShop(options.from, 'MMM D, h:mm A')} to ${formatShop(options.to, 'MMM D, h:mm A')}. The time stays the same.`}{' '}
            Only people who are free, working and skilled for it then are listed.
          </div>
          {people.length ? (
            <Radio.Group
              value={picked}
              onChange={(e) => setPicked(e.target.value)}
              style={{ display: 'grid', gap: 8 }}
            >
              {people.map((p) => (
                <Radio key={p.workerId} value={p.workerId}>
                  <strong>{p.fullName}</strong>
                  <span style={{ color: '#64748b' }}> · {p.note}</span>
                </Radio>
              ))}
            </Radio.Group>
          ) : (
            <Alert
              type="warning"
              showIcon
              message="Nobody qualified is free at that time."
              description="Use Reschedule to move the work to a time when someone is free."
            />
          )}
        </>
      ) : null}
    </Modal>
  );
}
