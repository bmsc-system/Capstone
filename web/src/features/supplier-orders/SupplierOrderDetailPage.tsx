import { formatShop, isFutureShopDay, isPastShopDay, shopToday } from '../../utils/shopTime';
import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, DatePicker, Input, InputNumber, Select, Space, Table, Tooltip, message } from 'antd';
import type { TableColumnsType } from 'antd';
import {
  CheckOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  PrinterOutlined,
  StopOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  InboxOutlined,
  RollbackOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { Link, useNavigate, useParams } from 'react-router-dom';
import dayjs, { type Dayjs } from 'dayjs';
import { supplierOrdersApi } from '../../api/supplierOrders.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { PHONE_QUERY, useMediaQuery } from '../../hooks/useViewport';
import OverdueTag from '../../components/OverdueTag';
import StatusPill from '../../components/StatusPill';
import { Page, PageSkeleton, SectionCard, FormModal } from '../../components/ui';
import { formatPoMaterialLabel } from '../../components/MaterialInputs';
import { SIZE_PLACEHOLDER } from '../../components/materialPicker';
import type { DeliveryMode, MaterialPurchase, SupplierOrder } from '../../types';
import OrderMaterialsModal from './OrderMaterialsModal';
import { LINE_STATUS_PILL, ORDER_STATUS_PILL, fmtDay, fmtMoney, fmtQty } from './supplierOrderUi';
import {
  PRINT_BEFORE_RECEIVE_HINT,
  canReceiveOrder,
} from './printReceiveGate';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import './supplierOrderDetail.css';
import { confirmAction } from '../../utils/confirmAction';
import { DialogField, DialogFields } from '../../utils/DialogField';

const DELIVERY_MODE_OPTIONS: { value: DeliveryMode; label: string }[] = [
  { value: 'PICKUP', label: 'Pick-up' },
  { value: 'DELIVERY', label: 'Delivery' },
];

function askDate(title: string, intro: string, okText: string, onOk: (d: string) => Promise<void>) {
  let picked: Dayjs = shopToday();
  confirmAction({
    wide: true,
    icon: <InboxOutlined />,
    title,
    content: (
      <DialogFields intro={intro}>
        <DialogField label="Date received">
          <DatePicker
            style={{ width: '100%' }}
            defaultValue={shopToday()}
            format="MMM D, YYYY"
            allowClear={false}
            disabledDate={isFutureShopDay}
            onChange={(d) => {
              if (d) picked = d;
            }}
          />
        </DialogField>
      </DialogFields>
    ),
    okText,
    onOk: () => onOk(picked.format('YYYY-MM-DD')),
  });
}

/** PO number as printed: supplier code + 2-digit year + 6-digit number. */
const poNumberOf = (code: string, year: number, seq: number) =>
  `${code}${String(year % 100).padStart(2, '0')}${String(seq).padStart(6, '0')}`;

/** Date issued and the PO number (prefilled with the next one; the shop's
 *  paper POs came first, so the first one can carry on from the last paper number). */
function IssueFields({
  intro,
  dateLabel,
  code,
  year,
  nextSeq,
  onDate,
  onSeq,
  startDate,
  startSeq,
}: {
  intro: string;
  dateLabel: string;
  code: string | null | undefined;
  year: number;
  nextSeq: number;
  onDate: (d: Dayjs) => void;
  onSeq: (n: number) => void;
  /** What the Office Staff asked for, shown to the Admin to confirm. */
  startDate?: Dayjs;
  startSeq?: number;
}) {
  const [seq, setSeq] = useState<number>(startSeq ?? nextSeq);
  return (
    <DialogFields intro={intro}>
      <DialogField label={dateLabel}>
        <DatePicker
          style={{ width: '100%' }}
          defaultValue={startDate ?? shopToday()}
          format="MMM D, YYYY"
          allowClear={false}
          disabledDate={isFutureShopDay}
          onChange={(d) => {
            if (d) onDate(d);
          }}
        />
      </DialogField>
      {code ? (
        <DialogField
          label="PO number"
          help={
            <>
              Prints as <strong>{poNumberOf(code, year, seq || nextSeq)}</strong>. Type another number to carry on
              from your paper POs; later ones continue from the highest number used.
            </>
          }
        >
          <Space.Compact style={{ width: '100%' }}>
            <Input
              disabled
              value={`${code}${String(year % 100).padStart(2, '0')}`}
              style={{ width: 96, textAlign: 'right' }}
              aria-label="Supplier code and year"
            />
            <InputNumber
              min={1}
              max={999999}
              precision={0}
              value={seq}
              style={{ flex: 1 }}
              aria-label="PO number"
              onChange={(v) => {
                const n = typeof v === 'number' ? v : nextSeq;
                setSeq(n);
                onSeq(n);
              }}
            />
          </Space.Compact>
        </DialogField>
      ) : null}
    </DialogFields>
  );
}

export default function SupplierOrderDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { canOfficeWrite, isAdmin } = useAuth();
  const phone = useMediaQuery(PHONE_QUERY);
  const [order, setOrder] = useState<SupplierOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editLine, setEditLine] = useState<MaterialPurchase | null>(null);
  const [editValues, setEditValues] = useState<{
    quantity: number | null;
    unitCost: number | null;
    gradeOrSpec: string;
    sizeSpec: string;
  }>({ quantity: null, unitCost: null, gradeOrSpec: '', sizeSpec: '' });
  const [notes, setNotes] = useState('');
  const [vatRate, setVatRate] = useState<number | null>(null);
  const [terms, setTerms] = useState('');
  const [dateOpen, setDateOpen] = useState(false);
  const [newDate, setNewDate] = useState<Dayjs | null>(null);
  const [dateNote, setDateNote] = useState('');

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const { data } = await supplierOrdersApi.get(id);
      setOrder(data);
      setNotes(data.notes || '');
      setVatRate(data.vatRate ?? null);
      setTerms(data.termsOfPayment || 'PDC');
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (fn: () => Promise<{ data: SupplierOrder }>, ok: string) => {
    setBusy(true);
    try {
      const { data } = await fn();
      setOrder((prev) => ({
        ...data,
        expectedDeliveryChanges: data.expectedDeliveryChanges ?? prev?.expectedDeliveryChanges,
      }));
      setNotes(data.notes || '');
      setVatRate(data.vatRate ?? null);
      message.success(ok);
    } catch (err) {
      message.error(getErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <PageSkeleton variant="detail" />
    );
  }
  if (!order) return <div style={{ padding: 24 }}>Supplier order not found.</div>;

  const isDraft = order.status === 'DRAFT';
  const awaiting = isDraft && Boolean(order.awaitingApproval);
  const receivable = order.status === 'ISSUED' || order.status === 'PARTIALLY_RECEIVED';
  const lines = order.lines || [];
  const pill = awaiting
    ? { label: 'Awaiting approval', color: 'amber' as const }
    : ORDER_STATUS_PILL[order.status];
  const editableDraft = isDraft && canOfficeWrite && !awaiting;
  const printed = canReceiveOrder(order.printedAt);
  const receivableByMe = receivable && canOfficeWrite;
  const cancelled = order.status === 'CANCELLED';
  const printTermsEditable = canOfficeWrite && (isDraft || receivable);

  const printPo = async () => {
    if (isDraft || cancelled) return;
    setBusy(true);
    try {
      // Office Staff: POST records the print; Admin can still open the view via GET route.
      if (canOfficeWrite) {
        const { data } = await supplierOrdersApi.markPrinted(order.id);
        setOrder((prev) =>
          prev
            ? {
                ...prev,
                printedAt: data.order.printedAt,
                printedById: data.order.printedById,
                printedByName: data.order.printedByName,
                printCount: data.order.printCount,
              }
            : prev
        );
      }
      navigate(`/supplier-orders/${order.id}/print`);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  /** Saves only the printed terms, leaving unsaved notes and VAT as typed. */
  const savePrintTerms = async (patch: { termsOfPayment?: string; deliveryMode?: DeliveryMode }) => {
    setBusy(true);
    try {
      const { data } = await supplierOrdersApi.update(order.id, patch);
      setOrder((prev) =>
        prev
          ? { ...prev, termsOfPayment: data.termsOfPayment, deliveryMode: data.deliveryMode }
          : data
      );
      setTerms(data.termsOfPayment || 'PDC');
      message.success('Saved');
    } catch (err) {
      message.error(getErrorMessage(err));
      setTerms(order.termsOfPayment || 'PDC');
    } finally {
      setBusy(false);
    }
  };
  const commitTerms = () => {
    const next = terms.trim();
    if (!next) {
      setTerms(order.termsOfPayment || 'PDC');
      return;
    }
    if (next !== (order.termsOfPayment || 'PDC')) void savePrintTerms({ termsOfPayment: next });
  };
  const draftDirty =
    editableDraft && ((order.notes || '') !== notes || (order.vatRate ?? null) !== vatRate);

  /** Office Staff: send the draft to the Admin with the PO number and date they want. */
  const sendForApproval = () => {
    let picked: Dayjs = shopToday();
    const nextSeq = order.nextPoSeq ?? 1;
    let seq = nextSeq;
    confirmAction({
      wide: true,
      icon: <SendOutlined />,
      title: 'Send for approval?',
      content: (
        <IssueFields
          intro={`${order.supplierName} order. The Admin approves it before the PO is issued. The lines lock while it waits; you can take it back to change it.`}
          dateLabel="Date to issue"
          code={order.supplierCode}
          year={order.nextPoYear ?? shopToday().year()}
          nextSeq={nextSeq}
          onDate={(d) => {
            picked = d;
          }}
          onSeq={(n) => {
            seq = n;
          }}
        />
      ),
      okText: 'Send for approval',
      onOk: () =>
        run(
          () =>
            supplierOrdersApi.submit(
              order.id,
              picked.format('YYYY-MM-DD'),
              order.supplierCode && seq !== nextSeq ? seq : undefined
            ),
          'Sent to the Admin for approval'
        ),
    });
  };

  const takeBack = () =>
    run(() => supplierOrdersApi.withdraw(order.id), 'Taken back. You can change it now.').catch(
      () => {}
    );

  /** Admin: approve what the Office Staff sent and issue the PO. */
  const issue = () => {
    const nextSeq = order.nextPoSeq ?? 1;
    const startSeq = order.requestedPoSeq ?? nextSeq;
    const startDate = order.requestedIssueDate ? dayjs(order.requestedIssueDate) : shopToday();
    let picked: Dayjs = startDate;
    let seq = startSeq;
    confirmAction({
      wide: true,
      icon: <CheckCircleOutlined />,
      title: 'Approve and issue?',
      content: (
        <IssueFields
          intro={`${order.supplierName} order. This assigns the PO number, sets the expected delivery date from the supplier's ${
            order.supplierLeadTimeDays ?? '—'
          }-day lead time, and locks the lines.`}
          dateLabel="Date issued"
          code={order.supplierCode}
          year={order.nextPoYear ?? shopToday().year()}
          nextSeq={nextSeq}
          startSeq={startSeq}
          startDate={startDate}
          onDate={(d) => {
            picked = d;
          }}
          onSeq={(n) => {
            seq = n;
          }}
        />
      ),
      okText: 'Approve and issue',
      onOk: () =>
        run(
          () =>
            supplierOrdersApi.issue(
              order.id,
              picked.format('YYYY-MM-DD'),
              order.supplierCode && seq !== nextSeq ? seq : undefined
            ),
          'Order approved and issued'
        ),
    });
  };

  const sendBack = () => {
    let note = '';
    confirmAction({
      icon: <RollbackOutlined />,
      title: 'Send this order back to Office Staff?',
      content: (
        <DialogFields>
          <DialogField label="What should be changed?">
            <Input.TextArea
              rows={3}
              maxLength={1000}
              placeholder="For example: reduce the quantity of the round bar to 4 pcs"
              onChange={(e) => {
                note = e.target.value;
              }}
            />
          </DialogField>
        </DialogFields>
      ),
      okText: 'Send back',
      onOk: async () => {
        if (!note.trim()) {
          message.warning('Say what should be changed.');
          throw new Error('note required');
        }
        await run(() => supplierOrdersApi.sendBack(order.id, note.trim()), 'Sent back to Office Staff');
      },
    });
  };

  const openDateChange = () => {
    setNewDate(order.expectedDeliveryDate ? dayjs(order.expectedDeliveryDate) : null);
    setDateNote('');
    setDateOpen(true);
  };

  const saveDateChange = async () => {
    if (!newDate || !dateNote.trim()) return;
    setBusy(true);
    try {
      const { data } = await supplierOrdersApi.changeExpectedDelivery(
        order.id,
        newDate.format('YYYY-MM-DD'),
        dateNote.trim()
      );
      setOrder(data);
      setDateOpen(false);
      const moved = data.movedJobs || [];
      const stuck = data.notMovedJobs || [];
      message.success(
        moved.length
          ? `Expected delivery updated. Moved later: ${moved.map((j) => j.jobNumber).join(', ')}`
          : 'Expected delivery updated. No job needed to move.'
      );
      if (stuck.length) {
        message.warning(
          `No free slot found for ${stuck.map((j) => j.jobNumber).join(', ')}; their schedule was left as it is.`
        );
      }
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const openLines = lines.filter((ln) => !ln.cancelledAt && !ln.dateReceived);
  const receiveOrder = () =>
    askDate(
      `Confirm delivery of ${order.poNumber}?`,
      `Deliveries arrive complete: all ${openLines.length} open line${
        openLines.length === 1 ? '' : 's'
      } on this order are received on one date.`,
      'Confirm delivery',
      (d) => run(() => supplierOrdersApi.receive(order.id, d), 'Delivery confirmed')
    );

  // A draft was never sent to the supplier, so it is discarded, not cancelled.
  const cancelOrder = () =>
    confirmAction({
      title: isDraft ? 'Discard this draft order?' : 'Cancel this supplier order?',
      content: isDraft
        ? 'The draft is removed and its materials go back to "to order". Nothing was sent to the supplier.'
        : 'Its materials go back to "to order".',
      okText: isDraft ? 'Discard draft' : 'Cancel order',
      danger: true,
      icon: isDraft ? <DeleteOutlined /> : <CloseCircleOutlined />,
      cancelText: 'Keep',
      onOk: () =>
        run(() => supplierOrdersApi.cancel(order.id), isDraft ? 'Draft discarded' : 'Order cancelled'),
    });

  const removeLine = (ln: MaterialPurchase) =>
    run(() => supplierOrdersApi.removeLine(order.id, ln.id), 'Line removed').catch(() => {});

  const openEdit = (ln: MaterialPurchase) => {
    setEditLine(ln);
    setEditValues({
      quantity: ln.quantity,
      unitCost: ln.unitCost,
      gradeOrSpec: ln.gradeOrSpec || '',
      sizeSpec: ln.sizeSpec || '',
    });
  };

  const saveEdit = async () => {
    if (!editLine) return;
    if (!editValues.quantity || editValues.unitCost == null) {
      message.error('Quantity and unit cost are required');
      return;
    }
    try {
      await run(
        () =>
          supplierOrdersApi.updateLine(order.id, editLine.id, {
            quantity: editValues.quantity as number,
            unitCost: editValues.unitCost as number,
            gradeOrSpec: editValues.gradeOrSpec.trim() || null,
            sizeSpec: editValues.sizeSpec.trim() || null,
          }),
        'Line updated'
      );
      setEditLine(null);
    } catch {
      /* message already shown */
    }
  };

  const columns: TableColumnsType<MaterialPurchase> = [
    {
      title: 'Item description',
      key: 'material',
      render: (_: unknown, ln) => (
        // A floor so the description is not squeezed into three lines while
        // the narrow "For" column keeps spare room (tablets, small laptops).
        <div style={{ minWidth: 200, ...(ln.cancelledAt ? { textDecoration: 'line-through', color: '#94a3b8' } : {}) }}>
          {formatPoMaterialLabel(ln.materialName, ln.gradeOrSpec, ln.sizeSpec)}
        </div>
      ),
    },
    {
      title: 'Qty',
      key: 'qty',
      width: 110,
      align: 'right',
      render: (_: unknown, ln) => fmtQty(ln.quantity, ln.unit),
    },
    {
      title: 'Unit price',
      dataIndex: 'unitCost',
      width: 120,
      align: 'right',
      render: (v: number) => fmtMoney(v),
    },
    {
      title: 'Amount',
      dataIndex: 'lineTotal',
      width: 130,
      align: 'right',
      render: (v: number) => <strong>{fmtMoney(v)}</strong>,
    },
    {
      title: 'For',
      key: 'job',
      width: 190,
      render: (_: unknown, ln) =>
        ln.jobOrderId ? (
          <Tooltip title={ln.jobTitle || undefined}>
            <Link to={`/job-orders/${ln.jobOrderId}`} className="so-items__job">
              {ln.jobNumber}
            </Link>
          </Tooltip>
        ) : (
          <span className="so-doc__muted">Stock</span>
        ),
    },
    {
      title: 'Status',
      key: 'status',
      width: 110,
      render: (_: unknown, ln) => {
        const p = LINE_STATUS_PILL[ln.status || 'ORDERED'] || LINE_STATUS_PILL.ORDERED;
        return (
          <StatusPill color={p.color} compact>
            {p.label}
          </StatusPill>
        );
      },
    },
    {
      title: '',
      key: 'act',
      width: 80,
      render: (_: unknown, ln) => {
        if (!canOfficeWrite || !isDraft) return null;
        return (
          <Space size={0}>
            <Tooltip title="Edit line">
              <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(ln)} />
            </Tooltip>
            <Tooltip title="Remove line">
              <Button
                size="small"
                type="text"
                icon={<DeleteOutlined />}
                disabled={busy}
                onClick={() => removeLine(ln)}
              />
            </Tooltip>
          </Space>
        );
      },
    },
  ];

  const vatAmount = order.vatRate ? Math.round(order.subtotal * order.vatRate) / 100 : 0;
  const total = order.subtotal + vatAmount;
  const changes = order.expectedDeliveryChanges || [];
  const deliveryLabel =
    DELIVERY_MODE_OPTIONS.find((o) => o.value === (order.deliveryMode || 'DELIVERY'))?.label || 'Delivery';
  // Line status only when lines differ (e.g. one cancelled); otherwise the order status says it.
  const showLineStatus = new Set(lines.map((ln) => ln.status || 'ORDERED')).size > 1;
  const tableColumns = columns.filter(
    (c) => c.key !== 'received' && (showLineStatus || c.key !== 'status'),
  );

  const expectedCell = (
    <div className="so-doc__value-row">
      <div>
        {!order.expectedDeliveryDate ? (
          <span className="so-doc__muted">
            Issue date + {order.supplierLeadTimeDays ?? '—'} days
          </span>
        ) : (
          <strong>{fmtDay(order.expectedDeliveryDate)}</strong>
        )}
        {order.originalExpectedDeliveryDate &&
        order.originalExpectedDeliveryDate !== order.expectedDeliveryDate ? (
          <Tooltip
            title={
              changes.length ? (
                <div>
                  {changes.map((c, i) => (
                    <div key={`${c.changedAt}-${i}`} style={{ marginBottom: 4 }}>
                      {fmtDay(c.from)} → {fmtDay(c.to)}
                      {c.note ? ` · ${c.note}` : ''}
                      <div style={{ opacity: 0.75, fontSize: 11 }}>
                        {c.changedByName || 'Unknown'}
                        {c.changedAt ? `, ${formatShop(c.changedAt, 'MMM D, YYYY, h:mm A')}` : ''}
                      </div>
                    </div>
                  ))}
                </div>
              ) : undefined
            }
          >
            <div className="so-doc__sub">
              Promised {fmtDay(order.originalExpectedDeliveryDate)}
              {changes.length ? ` · changed ${changes.length}×` : ''}
            </div>
          </Tooltip>
        ) : null}
        {order.daysOverdue ? (
          <div style={{ marginTop: 4 }}>
            <OverdueTag
              days={order.daysOverdue}
              tooltip="Not received by the expected date. Follow up with the supplier."
            />
          </div>
        ) : null}
      </div>
      {receivable && canOfficeWrite ? (
        <Tooltip title="Change expected delivery date">
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            aria-label="Change expected delivery date"
            disabled={busy}
            onClick={openDateChange}
          />
        </Tooltip>
      ) : null}
    </div>
  );

  return (
    <Page className="so-detail-page">
      <section className="ui-card so-doc">
        <header className="so-doc__top">
          <div className="so-doc__title">
            <span className="so-doc__kicker">
              {order.poNumber ? 'Purchase order' : 'Draft purchase order'}
            </span>
            <div className="so-doc__title-row">
              <h1>{order.poNumber || 'No PO number yet'}</h1>
              <StatusPill color={pill.color}>{pill.label}</StatusPill>
            </div>
          </div>
          <Space wrap className="so-doc__actions m-action-bar">
            {editableDraft ? (
              <Button icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>
                Add lines
              </Button>
            ) : null}
            {isDraft && canOfficeWrite && !awaiting ? (
              <Tooltip title={draftDirty ? 'Save your changes first' : undefined}>
                <Button
                  type="primary"
                  icon={<CheckOutlined />}
                  disabled={busy || lines.length === 0 || draftDirty}
                  onClick={sendForApproval}
                >
                  Send for approval
                </Button>
              </Tooltip>
            ) : null}
            {awaiting && canOfficeWrite ? (
              <Button disabled={busy} onClick={takeBack}>
                Take back to change
              </Button>
            ) : null}
            {isDraft && isAdmin ? (
              <Tooltip
                title={!awaiting ? 'Office Staff have not sent this order for approval yet.' : undefined}
              >
                <Button
                  type="primary"
                  icon={<CheckOutlined />}
                  disabled={busy || !awaiting || lines.length === 0}
                  onClick={issue}
                >
                  Approve and issue
                </Button>
              </Tooltip>
            ) : null}
            {awaiting && isAdmin ? (
              <Button disabled={busy} onClick={sendBack}>
                Send back
              </Button>
            ) : null}
            {!isDraft && !cancelled ? (
              <Button
                type={printed ? 'default' : 'primary'}
                icon={<PrinterOutlined />}
                disabled={busy}
                onClick={() => void printPo()}
              >
                Print PO
              </Button>
            ) : null}
            {receivableByMe ? (
              <Tooltip title={!printed ? PRINT_BEFORE_RECEIVE_HINT : undefined}>
                <Button
                  type={printed ? 'primary' : 'default'}
                  icon={<CheckOutlined />}
                  disabled={!openLines.length || busy || !printed}
                  onClick={receiveOrder}
                >
                  Confirm delivery
                </Button>
              </Tooltip>
            ) : null}
            {canOfficeWrite && (order.status === 'DRAFT' || order.status === 'ISSUED') ? (
              <Button danger icon={<StopOutlined />} disabled={busy} onClick={cancelOrder}>
                {isDraft ? 'Discard draft' : 'Cancel order'}
              </Button>
            ) : null}
          </Space>
        </header>

        {awaiting ? (
          <Alert
            type="info"
            showIcon
            style={{ margin: '16px 0' }}
            title="Waiting for the Admin's approval"
            description={
              [
                order.submittedByName || order.submittedAt
                  ? `Sent${order.submittedByName ? ` by ${order.submittedByName}` : ''}${
                      order.submittedAt ? ` on ${formatShop(order.submittedAt, 'MMM D, h:mm A')}` : ''
                    }.`
                  : '',
                canOfficeWrite
                  ? 'The lines are locked while it waits. Take it back if something needs to change.'
                  : isAdmin
                    ? 'Check the lines and totals, then approve and issue it, or send it back with what to change.'
                    : '',
              ]
                .filter(Boolean)
                .join(' ') || undefined
            }
          />
        ) : null}
        {isDraft && !awaiting && order.returnNote ? (
          <Alert
            type="warning"
            showIcon
            style={{ margin: '16px 0' }}
            title="The Admin sent this order back"
            description={order.returnNote}
          />
        ) : null}
        <div className="so-doc__grid">
          <div className="so-doc__block">
            <div className="so-doc__label">Supplier</div>
            <div className="so-doc__supplier">{order.supplierName}</div>
            {order.supplierAddress ? <div className="so-doc__sub">{order.supplierAddress}</div> : null}
            {order.supplierContactPerson || order.supplierPhone ? (
              <div className="so-doc__sub">
                {[order.supplierContactPerson, order.supplierPhone].filter(Boolean).join(' · ')}
              </div>
            ) : null}
          </div>
          <dl className="so-doc__facts">
            <div>
              <dt>Date issued</dt>
              <dd>{order.dateIssued ? fmtDay(order.dateIssued) : <span className="so-doc__muted">On issue</span>}</dd>
            </div>
            <div>
              <dt>Expected delivery</dt>
              <dd>{expectedCell}</dd>
            </div>
            {order.receivedDate ? (
              <div>
                <dt>Received</dt>
                <dd>{fmtDay(order.receivedDate)}</dd>
              </div>
            ) : !isDraft && !cancelled ? (
              <div>
                <dt>Printed</dt>
                <dd>
                  {printed ? (
                    <>
                      {fmtDay(order.printedAt)}
                      {order.printedByName ? (
                        <div className="so-doc__sub">by {order.printedByName}</div>
                      ) : null}
                    </>
                  ) : (
                    <StatusPill tone="amber" compact>
                      Not printed yet
                    </StatusPill>
                  )}
                </dd>
              </div>
            ) : null}
            <div>
              <dt>Total</dt>
              <dd className="so-doc__total ui-tabular">{fmtMoney(total)}</dd>
            </div>
          </dl>
        </div>

        <div className="so-doc__terms">
          <label className="so-doc__field">
            <span>Terms of payment</span>
            {printTermsEditable ? (
              <Input
                maxLength={100}
                value={terms}
                disabled={busy}
                onChange={(e) => setTerms(e.target.value)}
                onBlur={commitTerms}
                onPressEnter={commitTerms}
              />
            ) : (
              <strong>{order.termsOfPayment || 'PDC'}</strong>
            )}
          </label>
          <label className="so-doc__field">
            <span>Mode of delivery</span>
            {printTermsEditable ? (
              <Select<DeliveryMode>
                value={order.deliveryMode || 'DELIVERY'}
                disabled={busy}
                onChange={(v) => void savePrintTerms({ deliveryMode: v })}
                options={DELIVERY_MODE_OPTIONS}
              />
            ) : (
              <strong>{deliveryLabel}</strong>
            )}
          </label>
          {editableDraft ? (
            <label className="so-doc__field so-doc__field--vat">
              <span>VAT (optional)</span>
              <InputNumber
                min={0}
                max={100}
                placeholder="None"
                value={vatRate}
                onChange={(v) => setVatRate(v)}
                addonAfter="%"
                style={{ width: '100%' }}
              />
            </label>
          ) : null}
          {editableDraft ? (
            <label className="so-doc__field so-doc__field--notes">
              <span>Notes (not printed)</span>
              <Input.TextArea
                autoSize={{ minRows: 1, maxRows: 4 }}
                placeholder="For the office only"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </label>
          ) : order.notes ? (
            <div className="so-doc__field so-doc__field--notes">
              <span>Notes</span>
              <div>{order.notes}</div>
            </div>
          ) : null}
        </div>

        {draftDirty ? (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 16 }}
            title="Notes or VAT changed"
            action={
              <Button
                size="small"
                type="primary"
                loading={busy}
                onClick={() =>
                  run(() => supplierOrdersApi.update(order.id, { notes, vatRate }), 'Order saved').catch(
                    () => {}
                  )
                }
              >
                Save
              </Button>
            }
          />
        ) : null}

        {lines.some((ln) => ln.reviewAfterJobCancel) ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 16 }}
            title="A linked job was cancelled"
            description="One or more lines are flagged for review. Cancel this purchase order or keep the material for other work."
          />
        ) : null}
      </section>

      <SectionCard
        title="Items"
        subtitle={
          lines.length
            ? `${lines.length} line${lines.length === 1 ? '' : 's'}${
                !isDraft && !cancelled ? ' · locked after issue; received together on one date' : ''
              }`
            : undefined
        }
        flush
      >
        {phone ? (
          <div className="so-lines">
            {lines.length === 0 ? (
              <div className="so-lines__empty">
                {editableDraft ? 'No lines yet. Add lines to this draft.' : 'No lines yet.'}
              </div>
            ) : (
              lines.map((ln) => {
                const p = LINE_STATUS_PILL[ln.status || 'ORDERED'] || LINE_STATUS_PILL.ORDERED;
                return (
                  <article key={ln.id} className={`so-line${ln.cancelledAt ? ' is-cancelled' : ''}`}>
                    <div className="so-line__head">
                      <div className="so-line__title">
                        {formatPoMaterialLabel(ln.materialName, ln.gradeOrSpec, ln.sizeSpec)}
                      </div>
                      {showLineStatus ? (
                        <StatusPill color={p.color} compact>
                          {p.label}
                        </StatusPill>
                      ) : null}
                    </div>
                    {ln.jobOrderId || ln.toolCode ? (
                      <div className="so-line__job">
                        {ln.jobOrderId ? (
                          <Link to={`/job-orders/${ln.jobOrderId}`}>{ln.jobNumber}</Link>
                        ) : (
                          'Stock'
                        )}
                        {ln.jobOrderId && ln.jobTitle ? ` · ${ln.jobTitle}` : ''}
                      </div>
                    ) : null}
                    <div className="so-line__math ui-tabular">
                      <span>
                        {fmtQty(ln.quantity, ln.unit)} × {fmtMoney(ln.unitCost)}
                      </span>
                      <strong>{fmtMoney(ln.lineTotal ?? ln.quantity * ln.unitCost)}</strong>
                    </div>
                    {canOfficeWrite && isDraft ? (
                      <div className="so-line__foot">
                        <span />
                        <Space size={4}>
                          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(ln)}>
                            Edit
                          </Button>
                          <Button
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            aria-label="Remove line"
                            disabled={busy}
                            onClick={() => removeLine(ln)}
                          />
                        </Space>
                      </div>
                    ) : null}
                  </article>
                );
              })
            )}
            {lines.length ? (
              <dl className="so-totals ui-tabular">
                {order.vatRate ? (
                  <>
                    <div>
                      <dt>Subtotal</dt>
                      <dd>{fmtMoney(order.subtotal)}</dd>
                    </div>
                    <div>
                      <dt>VAT ({order.vatRate}%)</dt>
                      <dd>{fmtMoney(vatAmount)}</dd>
                    </div>
                  </>
                ) : null}
                <div className="so-totals__total">
                  <dt>Total</dt>
                  <dd>{fmtMoney(total)}</dd>
                </div>
              </dl>
            ) : null}
          </div>
        ) : (
          <ResponsiveTable<MaterialPurchase>
            className="so-items"
            card={{ title: 'material', subtitle: ['job'], badge: 'status', actions: 'act' }}
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={lines}
            columns={tableColumns}
            locale={{ emptyText: editableDraft ? 'No lines yet. Add lines to this draft.' : 'No lines yet.' }}
            summary={() =>
              lines.length ? (
                <>
                  {order.vatRate ? (
                    <>
                      <Table.Summary.Row className="so-items__sum">
                        <Table.Summary.Cell index={0} colSpan={3} align="right">
                          Subtotal
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={3} align="right">
                          {fmtMoney(order.subtotal)}
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={4} colSpan={tableColumns.length - 4} />
                      </Table.Summary.Row>
                      <Table.Summary.Row className="so-items__sum">
                        <Table.Summary.Cell index={0} colSpan={3} align="right">
                          VAT ({order.vatRate}%)
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={3} align="right">
                          {fmtMoney(vatAmount)}
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={4} colSpan={tableColumns.length - 4} />
                      </Table.Summary.Row>
                    </>
                  ) : null}
                  <Table.Summary.Row className="so-items__sum so-items__sum--total">
                    <Table.Summary.Cell index={0} colSpan={3} align="right">
                      Total
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={3} align="right">
                      {fmtMoney(total)}
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={4} colSpan={tableColumns.length - 4} />
                  </Table.Summary.Row>
                </>
              ) : null
            }
          />
        )}
      </SectionCard>

      <OrderMaterialsModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        defaultSupplierId={order.supplierId}
        onSaved={(saved) => {
          setAddOpen(false);
          if (saved.id === order.id) void load();
          else navigate(`/supplier-orders/${saved.id}`);
        }}
      />

      <FormModal
        size="sm"
        icon={<CalendarOutlined />}
        subtitle={order.poNumber || undefined}
        open={dateOpen}
        onCancel={() => setDateOpen(false)}
        onOk={saveDateChange}
        okText="Save"
        okButtonProps={{ disabled: !newDate || !dateNote.trim() }}
        confirmLoading={busy}
        title="Change expected delivery date"
        destroyOnHidden
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 13, color: '#475569' }}>
            For every line not yet received. A later date moves affected jobs later.
          </div>
          <div>
            <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>
              New expected delivery date
            </div>
            <DatePicker
              style={{ width: '100%' }}
              format="MMM D, YYYY"
              allowClear={false}
              value={newDate}
              onChange={(d) => setNewDate(d)}
              disabledDate={(d) =>
                isPastShopDay(d) ||
                (!!order.dateIssued && d.isBefore(dayjs(order.dateIssued), 'day'))
              }
            />
          </div>
          <div>
            <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>Note (required)</div>
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 4 }}
              maxLength={500}
              placeholder="Supplier confirmed delivery on 15 Oct"
              value={dateNote}
              onChange={(e) => setDateNote(e.target.value)}
            />
          </div>
        </div>
      </FormModal>

      <FormModal
        size="sm"
        icon={<EditOutlined />}
        open={!!editLine}
        onCancel={() => setEditLine(null)}
        onOk={saveEdit}
        okText="Save"
        confirmLoading={busy}
        title={editLine ? `Edit ${editLine.materialName}` : 'Edit line'}
        subtitle={editLine?.jobNumber ? `For ${editLine.jobNumber}` : undefined}
        destroyOnHidden
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {editLine && !editLine.toolId ? (
            <div>
              <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>Size</div>
              <Input
                placeholder={SIZE_PLACEHOLDER}
                value={editValues.sizeSpec}
                onChange={(e) => setEditValues((v) => ({ ...v, sizeSpec: e.target.value }))}
              />
            </div>
          ) : (
            <div>
              <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>Spec</div>
              <Input
                value={editValues.gradeOrSpec}
                onChange={(e) => setEditValues((v) => ({ ...v, gradeOrSpec: e.target.value }))}
              />
            </div>
          )}
          <div>
            <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>Quantity</div>
            <InputNumber
              min={0.0001}
              style={{ width: '100%' }}
              value={editValues.quantity}
              addonAfter={editLine?.unit}
              onChange={(q) => setEditValues((v) => ({ ...v, quantity: q }))}
            />
          </div>
          <div>
            <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>Unit cost</div>
            <InputNumber
              min={0}
              style={{ width: '100%' }}
              value={editValues.unitCost}
              onChange={(c) => setEditValues((v) => ({ ...v, unitCost: c }))}
            />
          </div>
        </div>
      </FormModal>
    </Page>
  );
}
