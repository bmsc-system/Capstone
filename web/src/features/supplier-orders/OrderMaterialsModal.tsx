import { formatShop } from '../../utils/shopTime';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, InputNumber, Modal, Select, Typography, message } from 'antd';
import { DeleteOutlined, PlusOutlined, ShoppingCartOutlined } from '@ant-design/icons';
import { catalogApi, type CatalogItem } from '../../api/catalog.api';
import { supplierOrdersApi } from '../../api/supplierOrders.api';
import { suppliersApi } from '../../api/suppliers.api';
import { MATERIAL_UNITS } from '../../api/materialCatalog.api';
import { getErrorMessage } from '../../api/client';
import type {
  DueConsumable,
  OutstandingMaterials,
  PlannedMaterialSummary,
  Supplier,
  SupplierOrder,
  SupplierOrderLineInput,
  SupplierReliability,
} from '../../types';
import { fmtDay, fmtQty } from './supplierOrderUi';
import { MaterialCatalogPicker } from '../../components/MaterialInputs';
import { SIZE_PLACEHOLDER } from '../../components/materialPicker';
import ResponsiveTable from '../../components/ui/ResponsiveTable';

const { Text } = Typography;

function reliabilityColor(pct: number) {
  if (pct >= 90) return '#15803d';
  if (pct >= 70) return '#b45309';
  return '#b91c1c';
}

/** "92% on time" (on or before the date promised at issue), or "Not enough deliveries". */
function ReliabilityText({ row }: { row?: SupplierReliability }) {
  if (!row) return null;
  if (!row.enoughData || row.reliabilityPct == null) {
    return <span style={{ fontSize: 12, color: '#94a3b8' }}>{row.label}</span>;
  }
  return (
    <span
      style={{ fontSize: 12, fontWeight: 600, color: reliabilityColor(row.reliabilityPct) }}
      title={`${row.onTimeDeliveries} of ${row.dueDeliveries} deliveries on or before the promised date${
        row.lateDeliveries ? `; late ones averaged ${row.avgDaysLate} days` : ''
      }`}
    >
      {row.label}
    </span>
  );
}

type PlannedEdit = { quantity: number | null; unitCost: number | null; gradeOrSpec: string };

/** A job material line picked from the catalog by Office Staff. */
type MaterialLineDraft = {
  key: number;
  jobOrderId?: string;
  /** The job's planned material this line orders (filled in from the job). */
  plannedMaterialId?: string | null;
  plannedCatalogId?: string | null;
  materialCatalogId: string | null;
  materialName: string;
  /** Undefined: look the grades up from the catalog by name. */
  grades?: string[];
  gradeOrSpec: string;
  sizeSpec: string;
  quantity: number | null;
  unit: string;
  unitCost: number | null;
};

let nextLineKey = 1;
const blankLine = (jobOrderId?: string): MaterialLineDraft => ({
  key: nextLineKey++,
  jobOrderId,
  materialCatalogId: null,
  materialName: '',
  grades: [],
  gradeOrSpec: '',
  sizeSpec: '',
  quantity: null,
  unit: 'pcs',
  unitCost: null,
});

/** Planned materials still to order become ready-filled lines (cost and supplier still to enter). */
export function linesFromPlanned(
  jobOrderId: string,
  planned: PlannedMaterialSummary[] = []
): MaterialLineDraft[] {
  return planned
    .filter(
      (p) =>
        p.materialCatalogId &&
        (p.status === 'TO_ORDER' || p.status === 'PARTLY_ORDERED') &&
        (p.remainingQuantity == null || p.remainingQuantity > 0)
    )
    .map((p) => ({
      ...blankLine(jobOrderId),
      plannedMaterialId: p.id,
      plannedCatalogId: p.materialCatalogId,
      materialCatalogId: p.materialCatalogId ?? null,
      materialName: p.name,
      grades: undefined,
      gradeOrSpec: p.gradeOrSpec || '',
      sizeSpec: p.sizeSpec || '',
      quantity: p.remainingQuantity ?? p.plannedQuantity ?? null,
      unit: p.unit || 'pcs',
    }));
}

const isBlank = (l: MaterialLineDraft) =>
  !l.materialCatalogId &&
  !l.materialName.trim() &&
  l.quantity == null &&
  l.unitCost == null &&
  !l.gradeOrSpec.trim() &&
  !l.sizeSpec.trim();

/** A consumable restock row: due to be bought again, or added from the consumables list. */
type ConsumableRow = {
  toolId: string;
  name: string;
  code: string;
  unit: string;
  sizeSpec: string | null;
  shopTerm?: string | null;
  due?: DueConsumable;
};

function fromDue(c: DueConsumable): ConsumableRow {
  return {
    toolId: c.toolId,
    name: c.name,
    code: c.code,
    unit: c.unit,
    sizeSpec: c.sizeSpec,
    shopTerm: c.shopTerm,
    due: c,
  };
}

function fromCatalogConsumable(item: CatalogItem): ConsumableRow {
  return {
    toolId: item.id,
    name: item.name,
    code: item.code || '',
    unit: item.unit,
    sizeSpec: item.sizeSpec || item.gradesOrSizes?.[0] || null,
    shopTerm: item.shopTerm,
  };
}

/**
 * The one path for purchasing: lines go onto the chosen supplier's open draft
 * order (a new draft is started if there is none). Job materials: Office Staff
 * pick each line from the catalog (size, quantity, unit, unit cost)
 * for a job whose materials are To order. Consumable restock: all offered
 * catalog consumables, including not-yet-stocked ones. With ``jobId`` the lines
 * are for that job only.
 */
export default function OrderMaterialsModal({
  open,
  onClose,
  onSaved,
  jobId,
  defaultSupplierId,
  planned,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (order: SupplierOrder) => void;
  jobId?: string;
  defaultSupplierId?: string | null;
  /** The job's materials needed; what is still to order starts the list. */
  planned?: PlannedMaterialSummary[];
}) {
  const plannedRef = useRef(planned);
  plannedRef.current = planned;
  const [, setFromPlan] = useState(0);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [reliability, setReliability] = useState<Record<string, SupplierReliability>>({});
  const [supplierId, setSupplierId] = useState<string | undefined>();
  const [data, setData] = useState<OutstandingMaterials | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [lines, setLines] = useState<MaterialLineDraft[]>([]);
  const [consumables, setConsumables] = useState<CatalogItem[]>([]);
  const [consumableRows, setConsumableRows] = useState<ConsumableRow[]>([]);
  const [selectedConsumables, setSelectedConsumables] = useState<string[]>([]);
  const [consumableEdits, setConsumableEdits] = useState<Record<string, PlannedEdit>>({});
  const offerConsumables = !jobId;

  useEffect(() => {
    if (!open) return;
    setSupplierId(defaultSupplierId || undefined);
    const prefilled = jobId ? linesFromPlanned(jobId, plannedRef.current) : [];
    setFromPlan(prefilled.length);
    setLines(prefilled.length ? prefilled : [blankLine(jobId)]);
    setSelectedConsumables([]);
    setConsumableEdits({});
    setConsumableRows([]);
    setLoading(true);
    suppliersApi
      .reliability()
      .then(({ data: rows }) => setReliability(Object.fromEntries(rows.map((r) => [r.supplierId, r]))))
      .catch(() => setReliability({}));
    if (offerConsumables) {
      // Every offered consumable can be added to the order.
      catalogApi
        .list({ type: 'CONSUMABLE' })
        .then(({ data: res }) => setConsumables(res.items.filter((i) => i.active)))
        .catch(() => setConsumables([]));
    }
    Promise.all([suppliersApi.list({ activeOnly: true }), supplierOrdersApi.outstanding(jobId)])
      .then(([s, o]) => {
        setSuppliers(s.data);
        setData(o.data);
        const low = o.data.dueConsumables || [];
        setConsumableRows(low.map(fromDue));
        setConsumableEdits(
          Object.fromEntries(
            low.map((c) => [
              c.toolId,
              {
                quantity: c.remainingSuggestedQuantity || c.usualQuantity || 1,
                unitCost: null,
                gradeOrSpec: c.sizeSpec || '',
              },
            ])
          )
        );
      })
      .catch((err) => message.error(getErrorMessage(err)))
      .finally(() => setLoading(false));
  }, [open, jobId, defaultSupplierId, offerConsumables]);

  const supplier = suppliers.find((s) => s.id === supplierId);
  const jobs = data?.jobs || [];
  const setLine = (key: number, patch: Partial<MaterialLineDraft>) =>
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const addLine = () =>
    setLines((prev) => [...prev, blankLine(jobId ?? prev[prev.length - 1]?.jobOrderId)]);
  const removeLine = (key: number) => setLines((prev) => prev.filter((l) => l.key !== key));
  const setConsumableEdit = (toolId: string, patch: Partial<PlannedEdit>) =>
    setConsumableEdits((prev) => ({ ...prev, [toolId]: { ...prev[toolId], ...patch } }));
  const addConsumable = (toolId: string) => {
    const item = consumables.find((t) => t.id === toolId);
    if (!item || consumableRows.some((r) => r.toolId === toolId)) return;
    const row = fromCatalogConsumable(item);
    setConsumableRows((prev) => [...prev, row]);
    setConsumableEdit(item.id, {
      quantity: 1,
      unitCost: null,
      gradeOrSpec: row.sizeSpec || '',
    });
    setSelectedConsumables((prev) => [...prev, item.id]);
  };
  const onSubmit = async () => {
    if (!supplierId) {
      message.error('Choose a supplier');
      return;
    }
    const payload: SupplierOrderLineInput[] = [];
    for (const [i, l] of lines.entries()) {
      if (isBlank(l)) continue;
      const name = l.materialName.trim();
      const where = `line ${i + 1}`;
      if (!l.jobOrderId) {
        message.error(`Choose the job for ${where}`);
        return;
      }
      if (!l.materialCatalogId) {
        message.error(`Pick a material from the catalog on ${where}`);
        return;
      }
      if (!l.quantity || l.quantity <= 0) {
        message.error(`Enter a quantity for ${name}`);
        return;
      }
      if (l.unitCost == null) {
        message.error(`Enter a unit cost for ${name}`);
        return;
      }
      payload.push({
        jobOrderId: l.jobOrderId,
        plannedMaterialId:
          l.plannedMaterialId && l.materialCatalogId === l.plannedCatalogId ? l.plannedMaterialId : null,
        materialCatalogId: l.materialCatalogId,
        materialName: name,
        gradeOrSpec: l.gradeOrSpec.trim() || null,
        sizeSpec: l.sizeSpec.trim() || null,
        quantity: l.quantity,
        unit: l.unit,
        unitCost: l.unitCost,
      });
    }
    for (const c of consumableRows) {
      if (!selectedConsumables.includes(c.toolId)) continue;
      const e = consumableEdits[c.toolId];
      if (!e?.quantity || e.quantity <= 0) {
        message.error(`Enter a quantity for ${c.name}`);
        return;
      }
      if (e.unitCost == null) {
        message.error(`Enter a unit cost for ${c.name}`);
        return;
      }
      payload.push({
        toolId: c.toolId,
        quantity: e.quantity,
        unit: c.unit,
        unitCost: e.unitCost,
        gradeOrSpec: e.gradeOrSpec.trim() || null,
      });
    }
    if (!payload.length) {
      message.error(
        offerConsumables ? 'Enter a material line or tick a consumable' : 'Enter at least one material line'
      );
      return;
    }
    try {
      setSaving(true);
      const { data: order } = await supplierOrdersApi.addDraftLines(supplierId, payload);
      message.success(`${payload.length} line${payload.length === 1 ? '' : 's'} added to the draft order`);
      onSaved(order);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={1040}
      centered
      destroyOnHidden
      footer={null}
      closable={false}
      className="app-form-modal"
      styles={{
        container: { padding: 0, borderRadius: 0, overflow: 'hidden' },
        body: { padding: 0 },
      }}
    >
      <div className="app-form-modal__head">
        <div className="app-form-modal__icon">
          <ShoppingCartOutlined />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="app-form-modal__title">
            {jobId ? 'Order materials for this job' : 'New supplier order'}
          </div>
        </div>
        <button type="button" className="app-form-modal__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div style={{ padding: '18px 24px 12px' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 13, color: '#475569', marginBottom: 6 }}>Supplier</div>
        <Select
          showSearch
          optionFilterProp="label"
          placeholder="Choose a supplier"
          style={{ width: 360 }}
          value={supplierId}
          onChange={setSupplierId}
          options={suppliers.map((s) => ({
            value: s.id,
            label: s.code ? `${s.name} (${s.code})` : s.name,
          }))}
          optionRender={(o) => (
            <span style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span>{o.label}</span>
              <ReliabilityText row={reliability[String(o.value)]} />
            </span>
          )}
          labelRender={(o) => (
            <span>
              {o.label}
              {o.value ? (
                <span style={{ marginLeft: 8 }}>
                  <ReliabilityText row={reliability[String(o.value)]} />
                </span>
              ) : null}
            </span>
          )}
        />
        {supplier ? (
          <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
            Lead time {supplier.typicalLeadTimeDays ?? '—'} day
            {supplier.typicalLeadTimeDays === 1 ? '' : 's'}. Lines are added to this supplier&apos;s
            open draft, or a new draft is started.
          </Text>
        ) : null}
      </div>

      {supplierId ? (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              margin: '8px 0',
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 13 }}>
              {jobId ? 'Raw materials for this job' : 'Raw materials'}
            </div>
            <Button size="small" icon={<PlusOutlined />} onClick={addLine} disabled={!jobs.length}>
              Add line
            </Button>
          </div>
          {!loading && !jobs.length ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 8 }}
              title={
                jobId
                  ? "This job's materials are Not required, so nothing is ordered for it."
                  : 'No open job has materials To order.'
              }
            />
          ) : (
            <ResponsiveTable<MaterialLineDraft>
              size="small"
              loading={loading}
              rowKey="key"
              pagination={false}
              scroll={{ y: 'min(320px, calc(100vh - 470px))' }}
              dataSource={lines}
              locale={{ emptyText: 'Add a line for each material to order.' }}
              columns={[
                ...(jobId
                  ? []
                  : [
                      {
                        title: 'Job',
                        key: 'job',
                        width: 190,
                        render: (_: unknown, l: MaterialLineDraft) => (
                          <Select
                            size="small"
                            showSearch
                            optionFilterProp="label"
                            placeholder="Choose a job"
                            style={{ width: '100%' }}
                            value={l.jobOrderId}
                            onChange={(v: string) => setLine(l.key, { jobOrderId: v })}
                            popupMatchSelectWidth={false}
                            labelRender={(o) =>
                              jobs.find((j) => j.id === o.value)?.jobNumber ?? o.label
                            }
                            options={jobs.map((j) => ({
                              value: j.id,
                              label: `${j.jobNumber} · ${j.title}`,
                              title: [
                                j.clientName,
                                j.dueDate ? `due ${fmtDay(j.dueDate)}` : null,
                                j.notOrderedYet ? 'nothing issued yet' : null,
                              ]
                                .filter(Boolean)
                                .join(' · '),
                            }))}
                          />
                        ),
                      },
                    ]),
                {
                  title: 'Material',
                  key: 'material',
                  width: 200,
                  render: (_: unknown, l) => (
                    <MaterialCatalogPicker
                      catalogId={l.materialCatalogId}
                      materialName={[l.materialName, l.gradeOrSpec].filter(Boolean).join(' ')}
                      onPick={(item) =>
                        setLine(l.key, {
                          materialCatalogId: item.id,
                          materialName: item.name,
                          grades: item.grades || [],
                          gradeOrSpec: '',
                          unit: item.defaultUnit || l.unit,
                        })
                      }
                      onClear={() =>
                        setLine(l.key, {
                          materialCatalogId: null,
                          materialName: '',
                          grades: [],
                          gradeOrSpec: '',
                        })
                      }
                    />
                  ),
                },
                {
                  title: 'Size',
                  key: 'size',
                  width: 220,
                  render: (_: unknown, l) => (
                    <Input
                      size="small"
                      placeholder={SIZE_PLACEHOLDER}
                      value={l.sizeSpec}
                      disabled={!l.materialCatalogId}
                      onChange={(e) => setLine(l.key, { sizeSpec: e.target.value })}
                    />
                  ),
                },
                {
                  title: 'Quantity',
                  key: 'qty',
                  width: 90,
                  render: (_: unknown, l) => (
                    <InputNumber
                      size="small"
                      min={0.0001}
                      style={{ width: '100%' }}
                      value={l.quantity}
                      onChange={(v) => setLine(l.key, { quantity: v })}
                    />
                  ),
                },
                {
                  title: 'Unit',
                  key: 'unit',
                  width: 90,
                  render: (_: unknown, l) => (
                    <Select
                      size="small"
                      style={{ width: '100%' }}
                      value={l.unit}
                      disabled={!l.materialCatalogId}
                      onChange={(v: string) => setLine(l.key, { unit: v })}
                      options={MATERIAL_UNITS.map((u) => ({ value: u, label: u }))}
                    />
                  ),
                },
                {
                  title: 'Unit cost',
                  key: 'cost',
                  width: 110,
                  render: (_: unknown, l) => (
                    <InputNumber
                      size="small"
                      min={0}
                      style={{ width: '100%' }}
                      value={l.unitCost}
                      onChange={(v) => setLine(l.key, { unitCost: v })}
                    />
                  ),
                },
                {
                  title: '',
                  key: 'remove',
                  width: 40,
                  render: (_: unknown, l) => (
                    <Button
                      size="small"
                      type="text"
                      icon={<DeleteOutlined />}
                      aria-label="Remove line"
                      onClick={() => removeLine(l.key)}
                    />
                  ),
                },
              ]}
            />
          )}


          {offerConsumables ? (
            <>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 12,
                  margin: '18px 0 8px',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 13 }}>
                  Consumables to restock
                </div>
                <Select
                  showSearch
                  size="small"
                  optionFilterProp="label"
                  placeholder="Add another consumable"
                  style={{ width: 280 }}
                  value={null}
                  onChange={(v: string) => addConsumable(v)}
                  options={consumables
                    .filter((t) => !consumableRows.some((r) => r.toolId === t.id))
                    .map((t) => ({
                      value: t.id,
                      label: `${t.name}${t.shopTerm ? ` “${t.shopTerm}”` : ''}${
                        t.code ? ` (${t.code})` : ''
                      }`,
                    }))}
                />
              </div>
              <ResponsiveTable<ConsumableRow>
                size="small"
                loading={loading}
                rowKey="toolId"
                pagination={false}
                scroll={{ y: 'min(260px, calc(100vh - 520px))' }}
                dataSource={consumableRows}
                locale={{ emptyText: 'No consumable is due to be bought.' }}
                rowSelection={{
                  selectedRowKeys: selectedConsumables,
                  onChange: (keys) => setSelectedConsumables(keys as string[]),
                }}
                columns={[
                  {
                    title: 'Consumable',
                    key: 'name',
                    render: (_: unknown, c) => (
                      <div>
                        <div style={{ fontWeight: 600 }}>{c.name}</div>
                        <div style={{ fontSize: 11, color: '#64748b' }}>
                          {[c.shopTerm && `“${c.shopTerm}”`, c.code].filter(Boolean).join(' · ')}
                        </div>
                      </div>
                    ),
                  },
                  {
                    title: 'Due',
                    key: 'stock',
                    width: 190,
                    render: (_: unknown, c) =>
                      c.due ? (
                        <div style={{ fontSize: 12 }}>
                          <span style={{ color: c.due.status === 'OVERDUE' ? '#b91c1c' : '#b45309', fontWeight: 600 }}>
                            {c.due.status === 'OVERDUE' ? 'Was due' : 'Due'}{' '}
                            {formatShop(c.due.nextPurchaseOn, 'MMM D')}
                          </span>
                          {c.due.usualQuantity ? ` · usually ${fmtQty(c.due.usualQuantity, c.unit)}` : ''}
                          {c.due.onOrderQuantity > 0 ? (
                            <div style={{ color: '#64748b' }}>
                              {fmtQty(c.due.onOrderQuantity, c.unit)} already on order
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <span style={{ fontSize: 12, color: '#64748b' }}>Added</span>
                      ),
                  },
                  {
                    title: 'Size / spec',
                    key: 'grade',
                    width: 130,
                    render: (_: unknown, c) => (
                      <Input
                        size="small"
                        value={consumableEdits[c.toolId]?.gradeOrSpec}
                        onChange={(e) => setConsumableEdit(c.toolId, { gradeOrSpec: e.target.value })}
                      />
                    ),
                  },
                  {
                    title: 'Quantity',
                    key: 'qty',
                    width: 130,
                    render: (_: unknown, c) => (
                      <InputNumber
                        size="small"
                        min={0.01}
                        style={{ width: '100%' }}
                        value={consumableEdits[c.toolId]?.quantity}
                        onChange={(v) => setConsumableEdit(c.toolId, { quantity: v })}
                        addonAfter={c.unit}
                      />
                    ),
                  },
                  {
                    title: 'Unit cost',
                    key: 'cost',
                    width: 120,
                    render: (_: unknown, c) => (
                      <InputNumber
                        size="small"
                        min={0}
                        style={{ width: '100%' }}
                        value={consumableEdits[c.toolId]?.unitCost}
                        onChange={(v) => setConsumableEdit(c.toolId, { unitCost: v })}
                      />
                    ),
                  },
                ]}
              />
            </>
          ) : null}
        </>
      ) : (
        <Alert type="info" showIcon title="Choose a supplier first." />
      )}
      </div>

      <div className="app-form-modal__footer">
        <Button onClick={onClose} style={{ minWidth: 96 }}>
          Cancel
        </Button>
        <Button
          type="primary"
          loading={saving}
          onClick={onSubmit}
          disabled={!supplierId}
          style={{ fontWeight: 700, minWidth: 120 }}
        >
          Save to draft order
        </Button>
      </div>
    </Modal>
  );
}
