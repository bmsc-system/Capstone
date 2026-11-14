import { formatShop } from '../../utils/shopTime';
import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Spin, message } from 'antd';
import { PrinterOutlined, ArrowLeftOutlined, EditOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { jobPrintBackPath } from './printNavigation';
import { clientsApi, jobOrdersApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { useShopDetails } from '../../hooks/useShopDetails';
import type { Client, JobOrder, Operation } from '../../types';
import { PrintDocument, SignatureBlock } from './PrintTemplate';
import { jobOrderPrintBlockers } from './jobOrderPrintReadiness';
import './jobOrderPrint.css';

const JOB_TYPE_LABEL: Record<string, string> = {
  FABRICATION: 'Fabrication',
  MACHINING: 'Machining',
  REPAIR: 'Repair',
};

const ROLE_TITLE: Record<string, string> = {
  ADMIN: 'Administrator',
  OFFICE_STAFF: 'Office Staff',
  PRODUCTION_WORKER: 'Production Worker',
};

function fmtDate(v?: string | null) {
  return v ? formatShop(v, 'MMMM D, YYYY') : '—';
}

function fmtDateTime(v?: string | null) {
  return v ? formatShop(v, 'MMM D, YYYY h:mm A') : '—';
}

/** "Nov 20, 8:00 AM" for the schedule's Start and End columns. */
function fmtStamp(v?: string | null) {
  return v ? formatShop(v, 'MMM D, h:mm A') : '—';
}

const PART_STAGE_LABEL: Record<string, string> = {
  RAW_MATERIAL: 'Raw material',
  CLIENT_SUPPLIED_ITEM: 'Client supplied item',
  WORK_IN_PROCESS: 'Work in process',
  CUT: 'Cut',
  BLANK: 'Blank',
  FORMED: 'Formed',
  MACHINED: 'Machined',
  ASSEMBLED: 'Assembled',
  HEAT_TREATED: 'Heat treated',
  FINISHED: 'Finished',
};

function orDash(v?: string | number | null) {
  return v == null || v === '' ? '—' : v;
}

function crewLines(op: Operation) {
  if (op.crew && op.crew.length > 1) {
    return op.crew.map((m) => (m.isLead ? `${m.fullName} (lead)` : m.fullName));
  }
  return [op.assignedWorkerName || '—'];
}

type MaterialRow = { key: string; name: string; spec: string; qty: number | null; unit: string };

function qtyText(qty: number | null, unit: string) {
  return qty != null ? `${qty}${unit ? ` ${unit}` : ''}` : '—';
}

/** What the job needs, one row per material: each planned material at its
    planned quantity (however it is being bought), plus lines bought without a
    plan. A line for the same material, size and unit as another row is the
    same need, so it counts once, at the larger quantity. */
function jobMaterialRows(job: JobOrder): MaterialRow[] {
  const rows = new Map<string, MaterialRow>();
  const add = (key: string, name: string, specParts: (string | null | undefined)[], qty: number | null, unit?: string | null) => {
    const spec = specParts.filter(Boolean).join(' · ');
    const u = unit || '';
    const same = `${name.trim().toLowerCase()}|${spec.trim().toLowerCase()}|${u.toLowerCase()}`;
    const prev = rows.get(same);
    if (prev) {
      if (qty != null && (prev.qty == null || qty > prev.qty)) prev.qty = qty;
      return;
    }
    rows.set(same, { key, name: name || '—', spec, qty, unit: u });
  };
  const planned = job.plannedMaterials || [];
  for (const p of planned) {
    const bought = (p.purchasedQuantity || 0) + (p.draftQuantity || 0);
    add(`plan-${p.id}`, p.name || '', [p.gradeOrSpec, p.sizeSpec], p.plannedQuantity ?? (bought > 0 ? bought : null), p.unit);
  }
  const plannedIds = new Set(planned.map((p) => p.id));
  for (const m of job.materialLines || []) {
    if (m.plannedMaterialId && plannedIds.has(m.plannedMaterialId)) continue;
    add(m.id, m.materialName || '', [m.gradeOrSpec, m.sizeSpec], m.quantity, m.unit);
  }
  return [...rows.values()];
}

export default function JobOrderPrintPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const shop = useShopDetails();
  const [job, setJob] = useState<JobOrder | null>(null);
  const [client, setClient] = useState<Client | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const { data } = await jobOrdersApi.get(id);
        if (cancelled) return;
        setJob(data);
        if (!data.clientName) {
          try {
            const { data: clients } = await clientsApi.list();
            if (!cancelled) setClient(clients.find((c) => c.id === data.clientId) || null);
          } catch {
            if (!cancelled) setClient(null);
          }
        }
      } catch (err) {
        if (!cancelled) message.error(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const ops = useMemo(
    () => [...(job?.operations || [])].sort((a, b) => a.sequenceNo - b.sequenceNo),
    [job]
  );

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!job) {
    return <div style={{ padding: 24 }}>Job order not found.</div>;
  }

  const materialRows = jobMaterialRows(job);
  const quantity =
    job.quantity != null ? `${job.quantity}${job.unitOfMeasure ? ` ${job.unitOfMeasure}` : ''}` : '—';
  const blockers = jobOrderPrintBlockers(job);
  const canPrint = blockers.length === 0;
  const plannedFinish = ops.reduce<string | null>(
    (latest, op) => (op.scheduledEnd && (!latest || op.scheduledEnd > latest) ? op.scheduledEnd : latest),
    null,
  );
  const preparedBy = job.createdByName || user?.fullName;
  const preparedRole = job.createdByName ? job.createdByRole : user?.role;
  const preparedTitle = preparedRole ? ROLE_TITLE[preparedRole] || null : null;

  return (
    <PrintDocument
      shop={shop}
      wide
      toolbar={
        <>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate(jobPrintBackPath())}
          >
            Back
          </Button>
          {user?.role === 'ADMIN' && (
            <Button icon={<EditOutlined />} onClick={() => navigate('/settings/shop')}>
              Edit shop details
            </Button>
          )}
          <Button
            type="primary"
            icon={<PrinterOutlined />}
            disabled={!canPrint}
            onClick={() => window.print()}
          >
            Print
          </Button>
        </>
      }
    >
      {!canPrint ? (
        <div className="no-print jo-print-blocked">
          <Alert
            type="warning"
            showIcon
            message="This job order can't be printed yet"
            description={
              <ul>
                {blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            }
          />
        </div>
      ) : null}
      <div className={canPrint ? undefined : 'jo-print-hidden'}>
      <div className="shop-print-doc-head">
        <h1 className="shop-print-doc-title">JOB ORDER</h1>
        <div className="shop-print-doc-ref">JO # {orDash(job.jobNumber)}</div>
        <div className="shop-print-doc-ref">
          <span className="shop-print-label">DATE</span> {fmtDate(job.createdAt)}
        </div>
      </div>

      <div className="shop-print-fields">
        <div>
          <span className="shop-print-label">Client:</span>{' '}
          {orDash(job.clientName || client?.name)}
        </div>
        <div>
          <span className="shop-print-label">Date required:</span> {fmtDate(job.dueDate)}
        </div>
        <div>
          <span className="shop-print-label">Client PO #:</span> {orDash(job.clientPoNumber)}
        </div>
        <div>
          <span className="shop-print-label">Planned finish:</span> {fmtDateTime(plannedFinish)}
        </div>
        <div>
          <span className="shop-print-label">Job type:</span>{' '}
          {orDash(job.jobType ? JOB_TYPE_LABEL[job.jobType] || job.jobType : null)}
        </div>
        <div>
          <span className="shop-print-label">Quantity:</span> {quantity}
        </div>
        {job.partCondition ? (
          <div className="shop-print-fields__full">
            <span className="shop-print-label">Stage of the part:</span>{' '}
            {PART_STAGE_LABEL[job.partCondition] || job.partCondition}
          </div>
        ) : null}
        <div className="shop-print-fields__full">
          <span className="shop-print-label">Title:</span> {orDash(job.title)}
        </div>
        <div className="shop-print-fields__full" style={{ whiteSpace: 'pre-wrap' }}>
          <span className="shop-print-label">Description:</span> {orDash(job.description)}
        </div>
      </div>

      <div className="shop-print-section">Operations</div>
      <table className="shop-print-table shop-print-table--compact jo-print-ops">
        <colgroup>
          <col style={{ width: '5%' }} />
          <col style={{ width: '19%' }} />
          <col style={{ width: '13%' }} />
          <col style={{ width: '21%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '10%' }} />
        </colgroup>
        <thead>
          <tr>
            <th rowSpan={2}>#</th>
            <th rowSpan={2}>Operation</th>
            <th rowSpan={2}>Machine unit</th>
            <th rowSpan={2}>Crew</th>
            <th colSpan={2}>Schedule</th>
            <th rowSpan={2}>Target hrs</th>
          </tr>
          <tr>
            <th>Start</th>
            <th>End</th>
          </tr>
        </thead>
        <tbody>
          {ops.map((op) => (
            <tr key={op.id || op.sequenceNo}>
              <td className="mid">{op.sequenceNo}</td>
              <td>{orDash(op.operationName)}</td>
              <td>{op.machineUnitLabel || (op.machineTypeId ? '—' : 'No machine')}</td>
              <td>
                {crewLines(op).map((name) => (
                  <div key={name}>{name}</div>
                ))}
              </td>
              <td>{fmtStamp(op.scheduledStart)}</td>
              <td>{fmtStamp(op.scheduledEnd)}</td>
              <td className="num">{op.estimatedHours != null ? op.estimatedHours.toFixed(2) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="shop-print-section">Materials</div>
      {materialRows.length === 0 ? (
        <div>{job.materialStatus === 'NOT_REQUIRED' ? 'Not required.' : 'None listed yet.'}</div>
      ) : (
        <table className="shop-print-table shop-print-table--compact">
          <colgroup>
            <col style={{ width: '75%' }} />
            <col style={{ width: '25%' }} />
          </colgroup>
          <thead>
            <tr>
              <th>Material</th>
              <th>Quantity</th>
            </tr>
          </thead>
          <tbody>
            {materialRows.map((m) => (
              <tr key={m.key}>
                <td>
                  {m.name}
                  {m.spec ? <div className="jo-print-sub">{m.spec}</div> : null}
                </td>
                <td className="num">{qtyText(m.qty, m.unit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="shop-print-signatures">
        <SignatureBlock label="Prepared by:" name={preparedBy} title={preparedTitle} />
        <SignatureBlock
          label="Approved by:"
          name={shop.joApproverName}
          title={shop.joApproverTitle}
        />
      </div>

      <div className="jo-print-stamp">
        Printed {formatShop(new Date().toISOString(), 'MMM D, YYYY h:mm A')}
        {user?.fullName ? ` by ${user.fullName}` : ''}. The schedule can change after printing; the
        system has the latest.
      </div>
      </div>
    </PrintDocument>
  );
}
