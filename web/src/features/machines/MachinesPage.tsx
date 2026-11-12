import { isPastShopDay } from '../../utils/shopTime';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DatePicker,
  Input,
  Select,
  Modal,
  Form,
  Skeleton,
  message,
  Button,
  Checkbox,
} from 'antd';
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  WarningOutlined,
  DownOutlined,
  PlusOutlined,
  ClusterOutlined,
  LeftOutlined,
  RightOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { operationsApi } from '../../api/operations.api';
import { usersApi } from '../../api/users.api';
import { useAuth } from '../../hooks/useAuth';
import { PersonChip } from '../../components/PersonAvatar';
import { personLabel } from '../../utils/people';
import { getErrorMessage } from '../../api/client';
import { DOWNTIME_REASONS } from '../../constants/downtimeReasons';
import type { BreakdownWork, MachineInfo, MachineUnitStatus, User } from '../../types';
import BreakdownWorkModal from './BreakdownWorkModal';
import InfoTip from '../../components/InfoTip';
import {
  EmptyState,
  FilterBar,
  FilterSelect,
  Page,
  StatCard,
  StatGrid,
  FormModal,
} from '../../components/ui';
import MachineCard from './MachineCard';
import MachineDetailsDrawer from './MachineDetailsDrawer';
import { accentVars, machineArt, machineKind, type MachineKind } from './machineKinds';
import {
  cardStatus,
  formatOpenDuration,
  type CardStatus,
  type MachineActions,
} from './machineStatus';
import { confirmAction } from '../../utils/confirmAction';

type StatusFilter = CardStatus;

type TypeGroup = {
  typeId: string;
  typeName: string;
  kind: MachineKind;
  units: MachineUnitStatus[];
};

function sectionLabel(text: string) {
  return <div className="app-form-section">{text}</div>;
}

function typeIdOf(unit: MachineUnitStatus): string {
  return unit.machineTypeId || unit.machineTypeCode || 'other';
}

function unitSearchText(unit: MachineUnitStatus): string {
  const cur = unit.currentOperation;
  const nxt = unit.nextOperation;
  return [
    unit.label,
    unit.machineTypeName,
    unit.machineTypeCode,
    unit.defaultOperatorName,
    unit.defaultOperatorNickname,
    unit.openDowntime?.reason,
    unit.openDowntime?.reportedByName,
    cur?.operationName,
    cur?.jobNumber,
    cur?.assignedWorkerName,
    cur?.assignedWorkerNickname,
    nxt?.operationName,
    nxt?.jobNumber,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function groupByType(units: MachineUnitStatus[]): TypeGroup[] {
  const map = new Map<string, TypeGroup>();
  for (const unit of units) {
    const typeId = typeIdOf(unit);
    const existing = map.get(typeId);
    if (existing) {
      existing.units.push(unit);
    } else {
      map.set(typeId, {
        typeId,
        typeName: unit.machineTypeName || unit.machineTypeCode || 'Other',
        kind: machineKind(unit.machineTypeName, unit.machineTypeCode),
        units: [unit],
      });
    }
  }
  return Array.from(map.values()).sort((a, b) => a.typeName.localeCompare(b.typeName));
}

function statusSummary(units: MachineUnitStatus[]) {
  const c = { running: 0, breakdown: 0, idle: 0, retired: 0 };
  for (const u of units) c[cardStatus(u)] += 1;
  return c;
}

const disablePastDates = isPastShopDay;

export default function MachinesPage() {
  const navigate = useNavigate();
  const isAdmin = useAuth().user?.role === 'ADMIN';
  const [units, setUnits] = useState<MachineUnitStatus[]>([]);
  const [machineTypes, setMachineTypes] = useState<MachineInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter[]>([]);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [showRemoved, setShowRemoved] = useState(false);
  const [collapsedTypes, setCollapsedTypes] = useState<Record<string, boolean>>({});
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const [reportFor, setReportFor] = useState<MachineUnitStatus | null>(null);
  const [closeFor, setCloseFor] = useState<MachineUnitStatus | null>(null);
  const [repairFor, setRepairFor] = useState<MachineUnitStatus | null>(null);
  const [breakdownWork, setBreakdownWork] = useState<BreakdownWork | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [operatorFor, setOperatorFor] = useState<MachineUnitStatus | null>(null);
  const [workers, setWorkers] = useState<User[]>([]);
  const [saving, setSaving] = useState(false);
  const [reportForm] = Form.useForm();
  const [closeForm] = Form.useForm();
  const [repairForm] = Form.useForm();
  const [addForm] = Form.useForm();
  const [operatorForm] = Form.useForm();

  const fetchUnits = async (includeRemoved = showRemoved) => {
    setLoading((prev) => prev || units.length === 0);
    try {
      const { data } = await operationsApi.machineUnitStatus(includeRemoved);
      setUnits(data);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUnits();
  }, [showRemoved]);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await jobOrdersApi.machines();
        setMachineTypes(data || []);
      } catch {
        /* add form can still open; types load best-effort */
      }
    })();
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    (async () => {
      try {
        const { data } = await usersApi.list();
        setWorkers(
          data
            .filter(
              (u) =>
                (u.role === 'PRODUCTION_WORKER' || u.role === 'ADMIN') && u.status !== 'DISABLED'
            )
            .sort((a, b) => a.fullName.localeCompare(b.fullName))
        );
      } catch {
        /* operator picker best-effort */
      }
    })();
  }, [isAdmin]);

  useEffect(() => {
    if (!units.some((u) => u.down)) return;
    const t = window.setInterval(() => setNowMs(Date.now()), 30000);
    return () => window.clearInterval(t);
  }, [units]);

  const allGroups = useMemo(() => groupByType(units), [units]);

  const typesRef = useRef<HTMLDivElement>(null);
  const [typeEdges, setTypeEdges] = useState({ left: false, right: false });
  const measureTypes = useCallback(() => {
    const el = typesRef.current;
    if (!el) return;
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setTypeEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
  }, []);
  useEffect(() => {
    const el = typesRef.current;
    if (!el) return;
    measureTypes();
    const ro = new ResizeObserver(measureTypes);
    ro.observe(el);
    return () => ro.disconnect();
  }, [allGroups.length, measureTypes]);
  const scrollTypes = (dir: -1 | 1) => {
    const el = typesRef.current;
    el?.scrollBy({ left: dir * Math.max(160, el.clientWidth * 0.7), behavior: 'smooth' });
  };

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = units.filter((u) => {
      if (typeFilter && typeIdOf(u) !== typeFilter) return false;
      if (q && !unitSearchText(u).includes(q)) return false;
      if (statusFilter.length && !statusFilter.includes(cardStatus(u))) return false;
      return true;
    });
    return groupByType(filtered);
  }, [units, search, statusFilter, typeFilter]);

  const detailsUnit = detailsId ? units.find((u) => u.id === detailsId) ?? null : null;

  const warnScheduled = (count: number) => {
    if (!count) return;
    message.warning(
      `${count} operation${count === 1 ? ' is' : 's are'} still booked on this machine. Use Move booked work in its details.`,
    );
  };

  const submitReport = async () => {
    if (!reportFor) return;
    try {
      const values = await reportForm.validateFields();
      setSaving(true);
      const { data } = await operationsApi.openDowntime(
        reportFor.id,
        values.reason,
        values.note?.trim() || undefined,
        undefined,
        values.expectedRepairDate ? values.expectedRepairDate.format('YYYY-MM-DD') : null
      );
      message.success(`${reportFor.label} marked as down`);
      setReportFor(null);
      reportForm.resetFields();
      await fetchUnits();
      if (data.work?.operations.length) setBreakdownWork(data.work);
      else if (!data.work) warnScheduled(data.affectedCount || 0);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const submitClose = async () => {
    if (!closeFor?.openDowntime) return;
    try {
      const values = await closeForm.validateFields();
      setSaving(true);
      await operationsApi.closeDowntime(
        closeFor.openDowntime.id,
        values.note?.trim() || undefined
      );
      message.success('Breakdown closed');
      setCloseFor(null);
      closeForm.resetFields();
      await fetchUnits();
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const submitRepairDate = async () => {
    if (!repairFor?.openDowntime) return;
    try {
      const values = await repairForm.validateFields();
      setSaving(true);
      await operationsApi.setExpectedRepairDate(
        repairFor.openDowntime.id,
        values.expectedRepairDate ? values.expectedRepairDate.format('YYYY-MM-DD') : null
      );
      message.success('Expected repair date saved');
      setRepairFor(null);
      repairForm.resetFields();
      await fetchUnits();
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const retireUnit = (unit: MachineUnitStatus) => {
    confirmAction({
      title: `Remove ${unit.label}?`,
      content: 'For a scrapped or replaced machine. Its history is kept.',
      okText: 'Remove machine',
      danger: true,
      onOk: async () => {
        try {
          const { data } = await operationsApi.setMachineUnitActive(unit.id, false);
          message.success(`${unit.label} removed from shop floor`);
          if (!showRemoved) setDetailsId(null);
          await fetchUnits();
          const count = data.affectedCount || 0;
          if (count > 0) {
            message.warning(
              `${count} operation${count === 1 ? ' is' : 's are'} still booked on ${unit.label}. Reschedule their job orders.`,
            );
          }
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const restoreUnit = async (unit: MachineUnitStatus) => {
    try {
      await operationsApi.setMachineUnitActive(unit.id, true);
      message.success(`${unit.label} restored`);
      await fetchUnits();
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const submitAdd = async () => {
    try {
      const values = await addForm.validateFields();
      setSaving(true);
      const { data } = await operationsApi.createMachineUnit(
        values.machineTypeId,
        values.label?.trim() || undefined
      );
      message.success(`${data.label} added`);
      setAddOpen(false);
      addForm.resetFields();
      await fetchUnits();
      try {
        const { data: types } = await jobOrdersApi.machines();
        setMachineTypes(types || []);
      } catch {
        /* ignore */
      }
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const submitDefaultOperator = async () => {
    if (!operatorFor) return;
    try {
      const values = await operatorForm.validateFields();
      setSaving(true);
      await operationsApi.setMachineUnitDefaultOperator(
        operatorFor.id,
        values.defaultOperatorId || null
      );
      message.success(`Assigned operator updated for ${operatorFor.label}`);
      setOperatorFor(null);
      operatorForm.resetFields();
      await fetchUnits();
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  // The saved operator may no longer be in the worker list (disabled, or an
  // Admin from before Admins stopped working jobs): still show their name.
  const operatorOptions = useMemo(() => {
    const id = operatorFor?.defaultOperatorId;
    if (!id || workers.some((w) => w.id === id)) return workers;
    return [
      ...workers,
      {
        id,
        fullName: operatorFor?.defaultOperatorName || 'Former operator',
        nickname: operatorFor?.defaultOperatorNickname || null,
        photoVersion: operatorFor?.defaultOperatorPhotoVersion ?? null,
      } as (typeof workers)[number],
    ];
  }, [workers, operatorFor]);

  const actions: MachineActions = {
    onDetails: (u) => setDetailsId(u.id),
    onReport: (u) => {
      reportForm.resetFields();
      setReportFor(u);
    },
    onClose: (u) => {
      closeForm.resetFields();
      setCloseFor(u);
    },
    onSetRepairDate: (u) => {
      const d = u.openDowntime?.expectedRepairDate;
      repairForm.setFieldsValue({ expectedRepairDate: d ? dayjs(d) : null });
      setRepairFor(u);
    },
    onOpenSchedule: () => navigate('/schedule'),
    onBreakdownWork: async (u) => {
      try {
        const { data } = await operationsApi.breakdownWork(u.id);
        if (data.operations.length) setBreakdownWork(data);
        else message.info(`No unstarted work is booked on ${u.label}`);
      } catch (err) {
        message.error(getErrorMessage(err));
      }
    },
    onOpenJob: (jobOrderId) => navigate(`/job-orders/${jobOrderId}`),
    onRetire: retireUnit,
    onRestore: restoreUnit,
    onSetDefaultOperator: (u) => {
      operatorForm.setFieldsValue({ defaultOperatorId: u.defaultOperatorId || undefined });
      setOperatorFor(u);
    },
    onClearDefaultOperator: async (u) => {
      try {
        setSaving(true);
        await operationsApi.setMachineUnitDefaultOperator(u.id, null);
        message.success(`${u.label} is now open to all`);
        await fetchUnits();
      } catch (err) {
        message.error(getErrorMessage(err));
      } finally {
        setSaving(false);
      }
    },
  };

  const toggleType = (typeId: string) => {
    setCollapsedTypes((prev) => ({ ...prev, [typeId]: !prev[typeId] }));
  };

  const toggleStatus = (s: StatusFilter) => {
    setStatusFilter((prev) => (prev.length === 1 && prev[0] === s ? [] : [s]));
  };

  const pickType = (typeId: string | null) => {
    setTypeFilter((prev) => (typeId === null || prev === typeId ? null : typeId));
    if (typeId) setCollapsedTypes((prev) => ({ ...prev, [typeId]: false }));
  };

  const counts = useMemo(() => {
    const c = { total: 0, running: 0, idle: 0, breakdown: 0, pending: 0 };
    for (const u of units) {
      const s = cardStatus(u);
      if (s === 'retired') continue;
      c.total += 1;
      c[s] += 1;
      if (s === 'breakdown') c.pending += u.affectedCount || 0;
    }
    return c;
  }, [units]);

  const detailsGroup = detailsUnit
    ? allGroups.find((g) => g.typeId === typeIdOf(detailsUnit))
    : undefined;

  return (
    <Page className="machines-page">
      <StatGrid min={180}>
        <StatCard
          icon={<ClusterOutlined />}
          tone="slate"
          label="On the floor"
          value={counts.total}
          loading={loading}
          onClick={() => setStatusFilter([])}
          active={statusFilter.length === 0}
        />
        <StatCard
          icon={<PlayCircleOutlined />}
          tone="green"
          label="Running"
          value={counts.running}
          loading={loading}
          onClick={() => toggleStatus('running')}
          active={statusFilter.length === 1 && statusFilter[0] === 'running'}
        />
        <StatCard
          icon={<PauseCircleOutlined />}
          tone="slate"
          label="Idle"
          value={counts.idle}
          loading={loading}
          onClick={() => toggleStatus('idle')}
          active={statusFilter.length === 1 && statusFilter[0] === 'idle'}
        />
        <StatCard
          icon={<WarningOutlined />}
          tone="red"
          label="Breakdown"
          value={counts.breakdown}
          valueTone={counts.breakdown > 0 ? 'red' : undefined}
          hint={counts.pending > 0 ? `${counts.pending} op${counts.pending === 1 ? '' : 's'} to reschedule` : undefined}
          loading={loading}
          onClick={() => toggleStatus('breakdown')}
          active={statusFilter.length === 1 && statusFilter[0] === 'breakdown'}
        />
      </StatGrid>

      <div className="ui-card machines-page__toolbar">
        <FilterBar
          search={{ value: search, onChange: setSearch, placeholder: 'Search machine, job, worker…' }}
          filters={
            <>
              <FilterSelect<StatusFilter[]>
                label="Status"
                mode="multiple"
                allowClear
                maxTagCount="responsive"
                value={statusFilter}
                onChange={setStatusFilter}
                options={[
                  { value: 'running', label: 'Running' },
                  { value: 'idle', label: 'Idle' },
                  { value: 'breakdown', label: 'Breakdown' },
                  ...(showRemoved ? [{ value: 'retired' as const, label: 'Removed' }] : []),
                ]}
              />
              <Checkbox
                checked={showRemoved}
                onChange={(e) => setShowRemoved(e.target.checked)}
                style={{ whiteSpace: 'nowrap' }}
              >
                Show removed
              </Checkbox>
              <InfoTip
                label="About machines"
                content="Shop floor status at a glance, one section per machine type. Click a machine for its details: who is assigned, the current and next job, breakdowns and history."
              />
            </>
          }
          actions={
            isAdmin ? (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => {
                  addForm.resetFields();
                  setAddOpen(true);
                }}
              >
                Add machine
              </Button>
            ) : undefined
          }
        />
        {allGroups.length > 0 && (
          <div
            className={`mtypes-wrap${typeEdges.left ? ' can-left' : ''}${typeEdges.right ? ' can-right' : ''}`}
          >
          <button
            type="button"
            className="mtypes-wrap__arrow mtypes-wrap__arrow--left"
            onClick={() => scrollTypes(-1)}
            aria-label="Scroll machine types left"
            tabIndex={-1}
          >
            <LeftOutlined />
          </button>
          <div className="mtypes" role="group" aria-label="Machine types" ref={typesRef} onScroll={measureTypes}>
            <button
              type="button"
              className={`mtypes__chip${typeFilter === null ? ' is-active' : ''}`}
              aria-pressed={typeFilter === null}
              onClick={() => pickType(null)}
            >
              <span className="mtypes__name">All types</span>
              <span className="mtypes__count ui-tabular">{units.length}</span>
            </button>
            {allGroups.map((g) => (
              <button
                key={g.typeId}
                type="button"
                className={`mtypes__chip${typeFilter === g.typeId ? ' is-active' : ''}`}
                style={accentVars(g.kind)}
                aria-pressed={typeFilter === g.typeId}
                onClick={() => pickType(g.typeId)}
              >
                <img className="mtypes__art" src={machineArt(g.kind)} alt="" />
                <span className="mtypes__name">{g.typeName}</span>
                <span className="mtypes__count ui-tabular">{g.units.length}</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="mtypes-wrap__arrow mtypes-wrap__arrow--right"
            onClick={() => scrollTypes(1)}
            aria-label="Scroll machine types right"
            tabIndex={-1}
          >
            <RightOutlined />
          </button>
          </div>
        )}
      </div>

      {loading ? (
        <section className="mtype" aria-busy>
          <div className="mtype__grid">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="mcard mcard--skeleton">
                <Skeleton active avatar={{ shape: 'square', size: 72 }} paragraph={{ rows: 3 }} title={false} />
              </div>
            ))}
          </div>
        </section>
      ) : groups.length === 0 ? (
        <div className="ui-card">
          <EmptyState
            icon={<ClusterOutlined />}
            title="No machines match your filters"
            description="Clear the search, status or type filter to see every machine."
          />
        </div>
      ) : (
        <div className="mtypes-board">
          {groups.map((group) => {
            const collapsed = collapsedTypes[group.typeId] ?? false;
            const total = allGroups.find((g) => g.typeId === group.typeId)?.units.length ?? group.units.length;
            const s = statusSummary(group.units);
            const parts = [
              s.running ? { key: 'running', text: `${s.running} running` } : null,
              s.breakdown ? { key: 'breakdown', text: `${s.breakdown} breakdown` } : null,
              s.idle ? { key: 'idle', text: `${s.idle} idle` } : null,
              s.retired ? { key: 'retired', text: `${s.retired} removed` } : null,
            ].filter((p): p is { key: string; text: string } => p !== null);
            return (
              <section
                key={group.typeId}
                id={`mtype-${group.typeId}`}
                className={`mtype${collapsed ? ' mtype--collapsed' : ''}`}
                style={accentVars(group.kind)}
              >
                <button
                  type="button"
                  className="mtype__head"
                  onClick={() => toggleType(group.typeId)}
                  aria-expanded={!collapsed}
                >
                  <span className="mtype__art">
                    <img src={machineArt(group.kind)} alt="" />
                  </span>
                  <span className="mtype__titles">
                    <span className="mtype__name">{group.typeName}</span>
                    <span className="mtype__meta">
                      <span className="mtype__count ui-tabular">
                        {group.units.length === total
                          ? `${total} unit${total === 1 ? '' : 's'}`
                          : `${group.units.length} of ${total} units`}
                      </span>
                      {parts.map((p) => (
                        <span key={p.key} className={`mtype__sum mtype__sum--${p.key}`}>
                          {p.text}
                        </span>
                      ))}
                    </span>
                  </span>
                  <DownOutlined className="mtype__chevron" aria-hidden />
                </button>
                {!collapsed && (
                  <div className="mtype__grid">
                    {group.units.map((unit) => (
                      <MachineCard
                        key={unit.id}
                        unit={unit}
                        kind={group.kind}
                        nowMs={nowMs}
                        actions={actions}
                        canEdit={isAdmin}
                      />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      <MachineDetailsDrawer
        unit={detailsUnit}
        kind={detailsGroup?.kind ?? 'other'}
        typeName={detailsGroup?.typeName ?? detailsUnit?.machineTypeName ?? ''}
        nowMs={nowMs}
        actions={actions}
        onClose={() => setDetailsId(null)}
        canEdit={isAdmin}
      />

      <Modal
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        footer={null}
        width={560}
        centered
        destroyOnHidden
        className="app-form-modal"
        styles={{
          container: { padding: 0, borderRadius: 0, overflow: 'hidden' },
          body: { padding: 0 },
        }}
        closable={false}
      >
        <div className="app-form-modal__head">
          <div className="app-form-modal__icon">
            <ClusterOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">Add machine</div>
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={() => setAddOpen(false)}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <Form form={addForm} layout="vertical" style={{ padding: '20px 24px 8px' }}>
          {sectionLabel('Machine')}
          <Form.Item
            name="machineTypeId"
            label="Machine type"
            rules={[{ required: true, message: 'Choose a machine type' }]}
            style={{ marginBottom: 14 }}
          >
            <Select
              placeholder="Lathe, Milling, …"
              options={machineTypes
                .filter((t): t is MachineInfo & { id: string } => Boolean(t.id))
                .map((t) => ({
                  value: t.id,
                  label: t.name,
                }))}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="label" label="Label (optional)" style={{ marginBottom: 14 }}>
            <Input placeholder="Leave blank for next number" maxLength={64} />
          </Form.Item>
        </Form>

        <div className="app-form-modal__footer">
          <Button onClick={() => setAddOpen(false)} style={{ minWidth: 96 }}>
            Cancel
          </Button>
          <Button
            type="primary"
            loading={saving}
            onClick={submitAdd}
            style={{ fontWeight: 700, minWidth: 120 }}
          >
            Add machine
          </Button>
        </div>
      </Modal>

      <FormModal
        size="sm"
        icon={<WarningOutlined />}
        title="Mark as down"
        subtitle={reportFor ? `${reportFor.label}` : undefined}
        open={!!reportFor}
        onCancel={() => setReportFor(null)}
        onOk={submitReport}
        confirmLoading={saving}
        okText="Mark as down"
        destroyOnHidden
      >
        <Form form={reportForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="reason"
            label="Reason"
            rules={[{ required: true, message: 'Pick a reason' }]}
          >
            <Select
              placeholder="Why is it down?"
              options={DOWNTIME_REASONS}
            />
          </Form.Item>
          <Form.Item
            name="note"
            label="Note"
            dependencies={['reason']}
            rules={[
              ({ getFieldValue }) => ({
                required: getFieldValue('reason') === 'OTHER',
                whitespace: true,
                message: 'Describe the breakdown when the reason is Other',
              }),
            ]}
          >
            <Input.TextArea rows={3} placeholder="Anything the shop should know" />
          </Form.Item>
          <Form.Item
            name="expectedRepairDate"
            label="Expected repair date (optional)"
            extra="Leave empty if unknown."
          >
            <DatePicker
              style={{ width: '100%' }}
              format="ddd, MMM D, YYYY"
              disabledDate={disablePastDates}
            />
          </Form.Item>
        </Form>
      </FormModal>

      <BreakdownWorkModal
        work={breakdownWork}
        canDecide={isAdmin}
        onClose={() => setBreakdownWork(null)}
        onDone={() => {
          setBreakdownWork(null);
          void fetchUnits();
        }}
        onOpenSchedule={() => navigate('/schedule')}
      />

      <FormModal
        size="sm"
        icon={<CalendarOutlined />}
        title="Expected repair date"
        subtitle={repairFor ? `${repairFor.label}` : undefined}
        open={!!repairFor}
        onCancel={() => setRepairFor(null)}
        onOk={submitRepairDate}
        confirmLoading={saving}
        okText="Save"
        destroyOnHidden
      >
        <Form form={repairForm} layout="vertical">
          <Form.Item name="expectedRepairDate" label="Expected repair date">
            <DatePicker
              allowClear
              style={{ width: '100%' }}
              format="ddd, MMM D, YYYY"
              disabledDate={disablePastDates}
            />
          </Form.Item>
        </Form>
      </FormModal>

      <FormModal
        size="sm"
        icon={<CheckCircleOutlined />}
        title="Close breakdown"
        subtitle={closeFor ? `${closeFor.label}` : undefined}
        open={!!closeFor}
        onCancel={() => setCloseFor(null)}
        onOk={submitClose}
        confirmLoading={saving}
        okText="Close breakdown"
        destroyOnHidden
      >
        {closeFor?.openDowntime && (
          <div style={{ fontSize: 13, color: '#64748b', marginBottom: 12 }}>
            {closeFor.openDowntime.reason}
            {closeFor.openDowntime.startedAt
              ? ` · down ${formatOpenDuration(closeFor.openDowntime.startedAt, nowMs)}`
              : ''}
            {closeFor.openDowntime.reportedByName
              ? ` · reported by ${closeFor.openDowntime.reportedByName}`
              : ''}
          </div>
        )}
        <Form form={closeForm} layout="vertical">
          <Form.Item name="note" label="Resolution note (optional)">
            <Input.TextArea rows={3} placeholder="What fixed it, parts used…" />
          </Form.Item>
        </Form>
      </FormModal>

      <FormModal
        size="sm"
        icon={<UserOutlined />}
        title={operatorFor ? `Operator · ${operatorFor.label}` : 'Operator'}
        open={!!operatorFor}
        onCancel={() => setOperatorFor(null)}
        onOk={submitDefaultOperator}
        confirmLoading={saving}
        okText="Save"
        destroyOnHidden
      >
        <Form form={operatorForm} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item
            name="defaultOperatorId"
            label="Usual operator"
            extra="Preferred when scheduling. Leave empty for anyone."
          >
            <Select
              allowClear
              showSearch
              optionFilterProp="search"
              placeholder="Anyone"
              options={operatorOptions.map((w) => ({
                value: w.id,
                search: personLabel(w.fullName, w.nickname),
                label: (
                  <PersonChip
                    userId={w.id}
                    fullName={w.fullName}
                    nickname={w.nickname}
                    photoVersion={w.photoVersion}
                  />
                ),
              }))}
            />
          </Form.Item>
        </Form>
      </FormModal>
    </Page>
  );
}
