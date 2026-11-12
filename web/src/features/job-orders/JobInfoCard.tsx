import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CalendarOutlined,
  DownOutlined,
  FileTextOutlined,
  InboxOutlined,
  NumberOutlined,
  ShopOutlined,
  UpOutlined,
  WarningFilled,
} from '@ant-design/icons';
import { IdChip, StatusPill } from '../../components/ui';
import { JOB_FLAG, JOB_STATUS } from '../../theme/statuses';
import type { Tone } from '../../theme/tokens';
import { formatShop, shopToday, toShopDayjs } from '../../utils/shopTime';
import type { JobOrder, ScheduleFlag } from '../../types';
import { fmtQty } from './MaterialOrdersSummary';

function enumLabel(v?: string | null) {
  return v ? v.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : '';
}

const peso = new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP' });
const fmtDate = (v?: string | null) => formatShop(v, 'MMM D, YYYY');

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/** "in 3 days" / "due today" / "2 days overdue", with how loud it should be. */
function dueUrgency(dueDate: string): { text: string; tone: Tone | null } {
  const days = toShopDayjs(dueDate).startOf('day').diff(shopToday().startOf('day'), 'day');
  if (days < 0) return { text: `${plural(-days, 'day')} overdue`, tone: 'red' };
  if (days === 0) return { text: 'due today', tone: 'red' };
  if (days <= 3) return { text: `in ${plural(days, 'day')}`, tone: 'amber' };
  return { text: `in ${plural(days, 'day')}`, tone: null };
}

/** Whole shop days the finish falls after the date required (0 if on time). */
function daysPastDue(finishIso: string, dueDate: string) {
  const finish = toShopDayjs(finishIso).startOf('day');
  return Math.max(0, finish.diff(toShopDayjs(dueDate).startOf('day'), 'day'));
}

type MaterialRow = {
  key: string;
  name: string;
  spec?: string | null;
  qty: string;
  state: { label: string; tone: Tone };
  detail?: string;
  orderId?: string | null;
  poNumber?: string | null;
};

/**
 * One list of what the job needs: every ordered line with its real state,
 * plus any planned material (earlier records) that no order covers yet.
 * Planned items already on an order are left out; the line stands for them.
 */
function materialRows(job: JobOrder): MaterialRow[] {
  const orders = job.materialReadiness?.supplierOrders || [];
  const overdue = new Map((job.materialReadiness?.lines || []).map((l) => [l.purchaseId, l.daysOverdue || 0]));
  const rows: MaterialRow[] = (job.materialLines || []).map((l) => {
    const order = orders.find((o) => o.poNumber && o.poNumber === l.poNumber);
    const late = overdue.get(l.id) || 0;
    let state: MaterialRow['state'];
    let detail: string | undefined;
    if (l.status === 'DRAFT') {
      state = { label: 'On draft PO', tone: 'slate' };
      detail = 'Not issued yet';
    } else if (l.status === 'CONSUMED') {
      state = { label: 'Used', tone: 'slate' };
    } else if (l.status === 'RECEIVED' || l.dateReceived) {
      state = { label: 'Received', tone: 'green' };
      detail = l.dateReceived ? fmtDate(l.dateReceived) : undefined;
    } else {
      state = late > 0 ? { label: `Ordered · ${plural(late, 'day')} late`, tone: 'red' } : { label: 'Ordered', tone: 'blue' };
      detail = l.expectedDate ? `Expected ${fmtDate(l.expectedDate)}` : undefined;
    }
    return {
      key: l.id,
      name: l.materialName,
      spec: [l.gradeOrSpec, l.sizeSpec].filter(Boolean).join(' · ') || undefined,
      qty: fmtQty(l.quantity, l.unit),
      state,
      detail,
      orderId: order?.supplierOrderId,
      poNumber: l.poNumber,
    };
  });

  const lineNames = new Set(rows.map((r) => r.name.trim().toLowerCase()));
  const summary = job.plannedMaterials;
  const planned = (job.rawMaterials || []).filter((m) => m.name?.trim());
  for (const m of planned) {
    const s = summary?.find((p) => p.id === m.id);
    if (s) {
      if (s.status === 'PURCHASED' || s.status === 'ON_DRAFT_ORDER') continue;
      if (s.status === 'FROM_STOCK') {
        rows.push({ key: m.id || m.name, name: m.name, qty: fmtQty(m.quantity, m.unit), state: { label: 'From stock', tone: 'green' } });
        continue;
      }
      rows.push({
        key: m.id || m.name,
        name: m.name,
        spec: [m.gradeOrSpec, m.sizeSpec].filter(Boolean).join(' · ') || undefined,
        qty: fmtQty(s.remainingQuantity ?? m.quantity, m.unit),
        state: { label: 'To order', tone: 'amber' },
        detail: s.status === 'PARTLY_ORDERED' ? 'Rest still to order' : undefined,
      });
    } else if (!lineNames.has(m.name.trim().toLowerCase())) {
      rows.push({
        key: m.id || m.name,
        name: m.name,
        qty: fmtQty(m.quantity, m.unit),
        state: m.fromStock ? { label: 'From stock', tone: 'green' } : { label: 'To order', tone: 'amber' },
      });
    }
  }
  return rows;
}

function materialSummary(job: JobOrder, rows: MaterialRow[]) {
  if (job.materialStatus === 'NOT_REQUIRED') return 'No materials to buy';
  if (!rows.length) return 'Materials to order · none listed';
  const counts = new Map<string, number>();
  for (const r of rows) {
    const k = r.state.label.startsWith('Ordered') ? 'ordered' : r.state.label.toLowerCase();
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return `Materials: ${[...counts].map(([k, n]) => `${n} ${k}`).join(', ')}`;
}

export type PlannedFinish = { projectedCompletion?: string | null; flag?: ScheduleFlag | null };

/** Job header for planning: who and what up front, key facts, materials, and whether the plan is late. */
export default function JobInfoCard({ job, finish }: { job: JobOrder; finish?: PlannedFinish | null }) {
  const [open, setOpen] = useState(true);
  const due = job.dueDate ? dueUrgency(job.dueDate) : null;
  const rows = materialRows(job);
  const description = job.description?.trim();
  const entered = job.createdAt
    ? `Entered${job.createdByName ? ` by ${job.createdByName}` : ''} · ${fmtDate(job.createdAt)}`
    : null;
  const lateBy =
    finish?.projectedCompletion && job.dueDate && finish.flag && finish.flag !== 'GREEN'
      ? daysPastDue(finish.projectedCompletion, job.dueDate)
      : 0;
  const flags: { label: string; tone: Tone }[] = [];
  if (job.needsReplan) flags.push(JOB_FLAG.needsReplan);
  if (job.waitingForMaterials) flags.push(JOB_FLAG.waitingForMaterials);

  const dueFact = job.dueDate ? (
    <span className={`jo-brief__fact${due?.tone ? ` is-${due.tone}` : ''}`}>
      <CalendarOutlined />
      <span>
        Due <strong>{formatShop(job.dueDate, 'MMM D')}</strong>
        {due ? ` · ${due.text}` : ''}
      </span>
    </span>
  ) : null;

  const lateNote =
    lateBy > 0 || finish?.flag === 'AMBER' || finish?.flag === 'RED' ? (
      <div className={`jo-brief__late is-${finish?.flag === 'RED' ? 'red' : 'amber'}`} role="status">
        <WarningFilled />
        <span>
          Proposed schedule finishes <strong>{formatShop(finish?.projectedCompletion, 'MMM D, YYYY HH:mm')}</strong>
          {lateBy > 0 ? `, ${plural(lateBy, 'day')} after the date required (${fmtDate(job.dueDate)})` : ', right at the date required'}
        </span>
      </div>
    ) : null;

  if (!open) {
    return (
      <section className="jo-brief is-collapsed" aria-label="Job information">
        <div className="jo-brief__line">
          <IdChip>{job.jobNumber || job.id.slice(0, 8).toUpperCase()}</IdChip>
          <span className="jo-brief__line-title">{job.title}</span>
          <span className="jo-brief__line-meta">{job.clientName || 'No client'}</span>
          {dueFact}
          {job.quantity != null ? (
            <span className="jo-brief__line-meta">{fmtQty(job.quantity, job.unitOfMeasure)}</span>
          ) : null}
          <span className="jo-brief__line-meta">{materialSummary(job, rows)}</span>
          {lateBy > 0 ? <StatusPill tone="red" compact>{`${plural(lateBy, 'day')} late`}</StatusPill> : null}
          <button type="button" className="jo-brief__toggle" onClick={() => setOpen(true)} aria-expanded={false}>
            Show details <DownOutlined />
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="jo-brief" aria-label="Job information">
      <div className="jo-brief__top">
        <div className="jo-brief__badges">
          <IdChip>{job.jobNumber || job.id.slice(0, 8).toUpperCase()}</IdChip>
          <StatusPill status={job.status} map={JOB_STATUS} compact />
          {job.jobType ? <StatusPill tone="slate" compact>{enumLabel(job.jobType)}</StatusPill> : null}
          {flags.map((f) => (
            <StatusPill key={f.label} tone={f.tone} compact>
              {f.label}
            </StatusPill>
          ))}
        </div>
        <button type="button" className="jo-brief__toggle" onClick={() => setOpen(false)} aria-expanded>
          Hide details <UpOutlined />
        </button>
      </div>
      <h2 className="jo-brief__title">{job.title}</h2>
      <div className="jo-brief__meta">
        <span className="jo-brief__client">
          <ShopOutlined /> {job.clientName || 'No client'}
        </span>
        {entered ? <span>{entered}</span> : null}
      </div>

      <div className="jo-brief__facts">
        {dueFact}
        <span className="jo-brief__fact">
          <NumberOutlined />
          <span>
            <strong>{job.quantity != null ? fmtQty(job.quantity, job.unitOfMeasure) : '—'}</strong>
          </span>
        </span>
        {job.amount != null ? (
          <span className="jo-brief__fact">
            <span className="jo-brief__peso" aria-hidden>₱</span>
            <span>
              <strong>{peso.format(job.amount).replace('₱', '')}</strong>
            </span>
          </span>
        ) : null}
        <span className="jo-brief__fact">
          <FileTextOutlined />
          <span>
            Client PO <strong>{job.clientPoNumber || '—'}</strong>
          </span>
        </span>
      </div>

      {lateNote}

      <div className={`jo-brief__body${description && description.length > 60 ? '' : ' is-stacked'}`}>
        <div className="jo-brief__block">
          <div className="jo-brief__label">Description</div>
          {description ? (
            <p className="jo-brief__desc">{description}</p>
          ) : (
            <p className="jo-brief__empty">No description added.</p>
          )}
        </div>
        <div className="jo-brief__block">
          <div className="jo-brief__label">
            <InboxOutlined /> Materials
          </div>
          {job.materialStatus === 'NOT_REQUIRED' ? (
            <p className="jo-brief__empty">Not required.</p>
          ) : rows.length ? (
            <ul className="jo-brief__mats">
              {rows.map((r) => (
                <li key={r.key}>
                  <span className="jo-brief__mat-name">
                    {r.name}
                    {r.spec ? <span className="jo-brief__mat-spec"> · {r.spec}</span> : null}
                  </span>
                  <span className="jo-brief__mat-qty">{r.qty}</span>
                  <span className="jo-brief__mat-state">
                    <StatusPill tone={r.state.tone} compact>
                      {r.state.label}
                    </StatusPill>
                    {r.detail ? <span className="jo-brief__mat-detail">{r.detail}</span> : null}
                  </span>
                  <span className="jo-brief__mat-po">
                    {r.orderId ? (
                      <Link to={`/supplier-orders/${r.orderId}`}>{r.poNumber || 'Supplier order'}</Link>
                    ) : r.poNumber ? (
                      r.poNumber
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="jo-brief__empty">To order: nothing listed yet.</p>
          )}
        </div>
      </div>
    </section>
  );
}
