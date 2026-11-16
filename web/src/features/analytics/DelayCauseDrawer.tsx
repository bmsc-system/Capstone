import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Drawer, Select, Table, Tag, message } from 'antd';
import type { TableColumnsType } from 'antd';
import dayjs from 'dayjs';
import { operationsApi } from '../../api/operations.api';
import { getErrorMessage } from '../../api/client';
import type { AnalyticsDelayCauseRow, AnalyticsDelayIncident, OperationPauseReason } from '../../types';
import { fmtNum } from './format';

/** Reasons the Admin can move a pause to. */
const PAUSE_REASON_OPTIONS: { value: OperationPauseReason; label: string }[] = [
  { value: 'MACHINE_DOWN', label: 'Machine breakdown' },
  { value: 'WAITING_MATERIAL', label: 'Waiting for material' },
  { value: 'WAITING_TOOL', label: 'Waiting for a tool' },
  { value: 'WAITING_INSTRUCTIONS', label: 'Waiting for drawing or instructions' },
  { value: 'WAITING_PRIOR_OPERATION', label: 'Waiting on prior operation' },
  { value: 'MOVED_TO_OTHER_JOB', label: 'Moved to another job' },
  { value: 'POWER_INTERRUPTION', label: 'Power interruption' },
  { value: 'WORKER_AWAY', label: 'Worker away' },
  { value: 'EMERGENCY', label: 'Emergency (had to leave)' },
  { value: 'BREAK', label: 'Break (not a delay)' },
  { value: 'END_OF_SHIFT', label: 'End of shift (not a delay)' },
  { value: 'OTHER', label: 'Other' },
];

const LABEL = Object.fromEntries(PAUSE_REASON_OPTIONS.map((o) => [o.value, o.label]));

function when(v: string | null | undefined) {
  if (!v) return '—';
  const d = dayjs(v);
  return v.length > 10 ? d.format('MMM D, h:mm A') : d.format('MMM D, YYYY');
}

/**
 * What is behind one bar of the delay Pareto: the pauses, breakdowns, late
 * materials or late outside work, newest first. The Admin can correct the
 * reason of a pause here (for example an "Other" pause that was a tool wait).
 */
export default function DelayCauseDrawer({
  cause,
  onClose,
  isAdmin,
  onRelabeled,
}: {
  cause: AnalyticsDelayCauseRow | null;
  onClose: () => void;
  isAdmin: boolean;
  onRelabeled: () => void;
}) {
  const [saving, setSaving] = useState<string | null>(null);
  const items = cause?.incidents ?? [];
  const kind = items[0]?.kind ?? (cause?.causeType as AnalyticsDelayIncident['kind'] | undefined);

  const relabel = async (row: AnalyticsDelayIncident, reason: OperationPauseReason) => {
    if (!row.logId) return;
    setSaving(row.logId);
    try {
      await operationsApi.relabelPause(row.logId, reason);
      message.success(`Moved to ${LABEL[reason] ?? reason}`);
      onRelabeled();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(null);
    }
  };

  const columns: TableColumnsType<AnalyticsDelayIncident> = [
    { title: kind === 'MATERIAL' ? 'First start' : kind === 'OUTSOURCED' ? 'Due back' : 'Started', key: 'w', width: 128, render: (_, r) => when(r.start) },
    {
      title: 'Job',
      key: 'j',
      width: 120,
      render: (_, r) =>
        r.jobOrderId ? <Link to={`/job-orders/${r.jobOrderId}`}>{r.jobNumber ?? 'Open job'}</Link> : <span className="ana-muted">—</span>,
    },
    {
      title: kind === 'MATERIAL' || kind === 'OUTSOURCED' ? (kind === 'MATERIAL' ? 'Supplier' : 'Outside shop') : 'Where',
      key: 'where',
      render: (_, r) =>
        r.kind === 'PAUSE' ? (
          <span>
            {[r.operation, r.machine].filter(Boolean).join(' · ') || '—'}
            {r.worker ? <div className="ana-muted">{r.worker}</div> : null}
          </span>
        ) : r.kind === 'DOWNTIME' ? (
          r.machine ?? '—'
        ) : (
          r.supplier ?? r.operation ?? '—'
        ),
    },
    { title: 'Hours', key: 'h', width: 72, align: 'right', render: (_, r) => fmtNum(r.hours, 1) },
    {
      title: 'Note',
      key: 'n',
      render: (_, r) => (
        <span>
          {r.note || <span className="ana-muted">—</span>}
          {r.originalReason ? (
            <Tag style={{ marginLeft: 6 }}>was {LABEL[r.originalReason] ?? r.originalReason}</Tag>
          ) : null}
        </span>
      ),
    },
  ];
  if (isAdmin && kind === 'PAUSE') {
    columns.push({
      title: 'Reason',
      key: 'r',
      width: 210,
      render: (_, r) => (
        <Select
          size="small"
          style={{ width: 196 }}
          value={cause?.cause as OperationPauseReason}
          loading={saving === r.logId}
          disabled={!r.logId || saving != null}
          options={PAUSE_REASON_OPTIONS}
          onChange={(v) => void relabel(r, v)}
          aria-label="Change pause reason"
        />
      ),
    });
  }

  const shown = items.length;
  const total = cause?.incidentCount ?? shown;
  return (
    <Drawer
      open={cause != null}
      onClose={onClose}
      title={cause ? `${cause.label} · ${fmtNum(cause.hours, 1)} h` : ''}
      size="large"
      destroyOnHidden
    >
      {cause?.action ? <Alert type="info" showIcon title="What to look at first" description={cause.action} style={{ marginBottom: 12 }} /> : null}
      <div className="ana-muted" style={{ marginBottom: 8 }}>
        {total} record{total === 1 ? '' : 's'} in this period
        {total > shown ? ` · showing the latest ${shown}` : ''}
        {isAdmin && kind === 'PAUSE' ? ' · change a reason if the worker picked the wrong one' : ''}
      </div>
      <Table<AnalyticsDelayIncident>
        size="small"
        rowKey={(r, i) => r.logId ?? `${r.jobOrderId}-${r.start}-${i}`}
        columns={columns}
        dataSource={items}
        pagination={items.length > 15 ? { pageSize: 15, showSizeChanger: false } : false}
        scroll={{ x: 640 }}
      />
    </Drawer>
  );
}
