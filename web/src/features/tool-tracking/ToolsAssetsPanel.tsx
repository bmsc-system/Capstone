import { formatShop, shopToday } from '../../utils/shopTime';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Col, DatePicker, Drawer, Dropdown, Form, Input, InputNumber, Modal, Row, Select, Skeleton, Space, Typography, message } from 'antd';
import type { MenuProps, TableColumnsType } from 'antd';
import {
  AppstoreAddOutlined,
  CloseOutlined,
  DownloadOutlined,
  HistoryOutlined,
  MoreOutlined,
  PlusOutlined,
  PrinterOutlined,
  QrcodeOutlined,
  SearchOutlined,
  SwapOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import { type Dayjs } from 'dayjs';
import { inventoryApi, toolsApi } from '../../api/tools.api';
import InfoTip from '../../components/InfoTip';
import apiClient, { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import type { InventoryUsageByWorker, ToolType, ToolUnit, ToolUnitStatus } from '../../types';
import { exportCsv } from '../../utils/csvExport';
import ToolEventsPage from './ToolEventsPage';
import QrLabelPrintModal from './QrLabelPrintModal';
import { downloadQrPng, type QrLabelItem } from './qrLabels';
import { useAuth } from '../../hooks/useAuth';
import { useIsPhone } from '../../hooks/useIsPhone';
import { MOBILE_SHELL_QUERY, useMediaQuery } from '../../hooks/useViewport';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { FormModal, TwoLineCell, rowOpenProps } from '../../components/ui';
import { Package, Wrench } from 'lucide-react';
import {
  CategoryTile,
  InventorySummaryCards,
  showSeedBadge,
  type InventorySummaryCard,
} from './inventoryLook';
import { confirmAction } from '../../utils/confirmAction';
import { newestFirst } from '../../utils/newestFirst';

function sectionLabel(text: string) {
  return <div className="app-form-section">{text}</div>;
}

function statusPill(status: ToolUnitStatus) {
  switch (status) {
    case 'AVAILABLE':
      return <StatusPill color="green" compact>Available</StatusPill>;
    case 'OUT':
      return <StatusPill color="amber" compact>Out</StatusPill>;
    case 'UNDER_REPAIR':
      return <StatusPill color="blue" compact>Repair</StatusPill>;
    case 'RETIRED':
      return <StatusPill color="gray" compact>Retired</StatusPill>;
    default:
      return status;
  }
}

export default function ToolsAssetsPanel() {
  const { canOfficeWrite: canEdit } = useAuth();
  const isPhone = useIsPhone();
  const compact = useMediaQuery(MOBILE_SHELL_QUERY);
  const [detailType, setDetailType] = useState<ToolType | null>(null);
  const [types, setTypes] = useState<ToolType[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [onlyOut, setOnlyOut] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [unitsByType, setUnitsByType] = useState<Record<string, ToolUnit[]>>({});
  const [typeModal, setTypeModal] = useState(false);
  const [unitModalType, setUnitModalType] = useState<ToolType | null>(null);
  const [editingType, setEditingType] = useState<ToolType | null>(null);
  const [editUnit, setEditUnit] = useState<ToolUnit | null>(null);
  const [qrUnit, setQrUnit] = useState<ToolUnit | null>(null);
  const [labelItems, setLabelItems] = useState<QrLabelItem[] | null>(null);
  const [labelPickOpen, setLabelPickOpen] = useState(false);
  const [labelPick, setLabelPick] = useState<string[]>([]);
  const [labelLoading, setLabelLoading] = useState(false);
  const [eventsOpen, setEventsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [typeForm] = Form.useForm();
  const [unitForm] = Form.useForm();
  const [editUnitForm] = Form.useForm();
  const unitQty = Form.useWatch('quantity', unitForm) ?? 1;
  const [creatingType, setCreatingType] = useState(false);
  const [creatingUnit, setCreatingUnit] = useState(false);
  const [savingUnit, setSavingUnit] = useState(false);
  const [usageRange, setUsageRange] = useState<[Dayjs, Dayjs]>([
    shopToday().subtract(29, 'day').startOf('day'),
    shopToday().endOf('day'),
  ]);
  const [usage, setUsage] = useState<InventoryUsageByWorker | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);
  const [summaryKey, setSummaryKey] = useState<string | null>(null);

  const fetchTypes = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await toolsApi.listTypes({ onlyWithOut: onlyOut });
      setTypes(newestFirst(data));
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [onlyOut]);

  const fetchUsage = async () => {
    setUsageLoading(true);
    try {
      const { data } = await inventoryApi.usageByWorker({
        from: usageRange[0].format('YYYY-MM-DD'),
        to: usageRange[1].format('YYYY-MM-DD'),
      });
      setUsage(data);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setUsageLoading(false);
    }
  };

  useEffect(() => {
    void fetchTypes();
  }, [fetchTypes]);

  useEffect(() => {
    if (historyOpen) void fetchUsage();
  }, [historyOpen]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return types.filter((t) => {
      if (summaryKey === 'borrowed' && !(t.outCount > 0)) return false;
      if (summaryKey === 'available' && !(t.availableCount > 0)) return false;
      if (
        q &&
        !(
          t.name.toLowerCase().includes(q) ||
          t.code.toLowerCase().includes(q) ||
          (t.description || '').toLowerCase().includes(q)
        )
      ) {
        return false;
      }
      return true;
    });
  }, [types, query, summaryKey]);

  const toolCards: InventorySummaryCard[] = useMemo(() => {
    const totalUnits = types.reduce((n, t) => n + (t.totalUnits || 0), 0);
    const available = types.reduce((n, t) => n + (t.availableCount || 0), 0);
    const borrowed = types.reduce((n, t) => n + (t.outCount || 0), 0);
    return [
      { key: 'types', label: 'Tool types', value: types.length, icon: Wrench, tone: 'slate' },
      { key: 'units', label: 'Total units', value: totalUnits, icon: Package, tone: 'blue' },
      { key: 'available', label: 'Available', value: available, icon: Package, tone: 'green' },
      { key: 'borrowed', label: 'Borrowed out', value: borrowed, icon: Package, tone: 'amber' },
    ];
  }, [types]);

  const unitLabel = (u: ToolUnit, type?: ToolType | null): QrLabelItem => ({
    key: u.id,
    code: u.assetCode,
    name: type?.name || u.toolTypeName || 'Tool',
    detail: type?.code || u.toolTypeCode || null,
  });

  /** Labels for every unit (not retired) of the chosen tool types. */
  const openLabelsForTypes = async (typeIds: string[]) => {
    setLabelLoading(true);
    try {
      const results = await Promise.all(typeIds.map((id) => toolsApi.getType(id)));
      const items = results.flatMap(({ data }) =>
        (data.units || [])
          .filter((u) => u.status !== 'RETIRED')
          .map((u) => unitLabel(u, data))
      );
      if (!items.length) {
        message.info('These tool types have no units to label yet.');
        return;
      }
      setLabelPickOpen(false);
      setLabelItems(items);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLabelLoading(false);
    }
  };

  const saveUnitPng = async (u: ToolUnit) => {
    try {
      await downloadQrPng(`/tools/units/${u.id}/qr`, unitLabel(u, types.find((t) => t.id === u.toolTypeId)));
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const loadUnits = async (typeId: string) => {
    try {
      const { data } = await toolsApi.getType(typeId);
      setUnitsByType((prev) => ({ ...prev, [typeId]: newestFirst(data.units || []) }));
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const onExpand = async (expanded: boolean, record: ToolType) => {
    if (expanded) {
      setExpandedKeys((k) => [...k, record.id]);
      await loadUnits(record.id);
    } else {
      setExpandedKeys((k) => k.filter((id) => id !== record.id));
    }
  };

  const openEditType = (t: ToolType) => {
    setEditingType(t);
    typeForm.setFieldsValue({ name: t.name, code: t.code, description: t.description || '' });
  };

  const closeTypeModal = () => {
    setTypeModal(false);
    setEditingType(null);
    typeForm.resetFields();
  };

  const createType = async (values: { name: string; code?: string; description?: string }) => {
    try {
      setCreatingType(true);
      if (editingType) {
        await toolsApi.updateType(editingType.id, values);
        message.success('Tool type saved');
      } else {
        await toolsApi.createType(values);
        message.success('Tool type added');
      }
      const typeId = editingType?.id;
      closeTypeModal();
      await fetchTypes();
      if (typeId && unitsByType[typeId]) await loadUnits(typeId);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setCreatingType(false);
    }
  };

  const createUnit = async (values: { quantity?: number; assetCode?: string; notes?: string }) => {
    if (!unitModalType) return;
    try {
      setCreatingUnit(true);
      const qty = Number(values.quantity) || 1;
      await toolsApi.createUnit(unitModalType.id, {
        quantity: qty,
        assetCode: qty > 1 ? undefined : values.assetCode,
        notes: values.notes,
      });
      message.success(qty > 1 ? `${qty} units added` : 'Unit added');
      setUnitModalType(null);
      unitForm.resetFields();
      await fetchTypes();
      await loadUnits(unitModalType.id);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setCreatingUnit(false);
    }
  };

  const saveUnit = async (values: { assetCode: string; notes?: string }) => {
    if (!editUnit) return;
    try {
      setSavingUnit(true);
      await toolsApi.updateUnit(editUnit.id, { assetCode: values.assetCode, notes: values.notes || '' });
      message.success('Unit saved');
      const typeId = editUnit.toolTypeId;
      setEditUnit(null);
      await fetchTypes();
      await loadUnits(typeId);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSavingUnit(false);
    }
  };

  const confirmRemoveUnit = (u: ToolUnit) =>
    confirmAction({
      icon: <DeleteOutlined />,
      title: `Remove ${u.assetCode}?`,
      content: 'If it was borrowed before, it is retired instead so its history is kept.',
      okText: 'Remove',
      danger: true,
      onOk: async () => {
        try {
          const { data } = await toolsApi.removeUnit(u.id);
          message.success(data.result === 'RETIRED' ? `${u.assetCode} retired (it has borrowing history)` : `${u.assetCode} removed`);
          await fetchTypes();
          await loadUnits(u.toolTypeId);
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });

  const confirmRemoveType = (t: ToolType) =>
    confirmAction({
      icon: <DeleteOutlined />,
      title: `Remove ${t.name}?`,
      content: t.totalUnits
        ? `Its ${t.totalUnits} unit${t.totalUnits === 1 ? ' is' : 's are'} removed too.`
        : undefined,
      okText: 'Remove',
      danger: true,
      onOk: async () => {
        try {
          await toolsApi.removeType(t.id);
          message.success(`${t.name} removed`);
          if (detailType?.id === t.id) setDetailType(null);
          await fetchTypes();
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });

  const openDetail = (t: ToolType) => {
    setDetailType(t);
    void loadUnits(t.id);
  };

  const unitMenuItems = (u: ToolUnit): MenuProps['items'] =>
    [
      {
        key: 'edit',
        label: 'Edit',
        onClick: () => {
          setEditUnit(u);
          editUnitForm.setFieldsValue({ assetCode: u.assetCode, notes: u.notes || '' });
        },
      },
      u.status !== 'AVAILABLE'
        ? { key: 'avail', label: 'Mark available', onClick: () => void setUnitStatus(u, 'AVAILABLE') }
        : null,
      u.status !== 'UNDER_REPAIR'
        ? { key: 'repair', label: 'Under repair', onClick: () => void setUnitStatus(u, 'UNDER_REPAIR') }
        : null,
      u.status !== 'RETIRED'
        ? { key: 'retire', label: 'Retire', onClick: () => void setUnitStatus(u, 'RETIRED') }
        : null,
      { type: 'divider' as const },
      { key: 'remove', label: 'Remove', danger: true, onClick: () => confirmRemoveUnit(u) },
    ].filter(Boolean) as MenuProps['items'];

  const setUnitStatus = async (unit: ToolUnit, status: ToolUnitStatus) => {
    try {
      await toolsApi.updateUnit(unit.id, { status });
      message.success('Unit updated');
      await fetchTypes();
      await loadUnits(unit.toolTypeId);
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const columns: TableColumnsType<ToolType> = [
    {
      title: 'Type',
      key: 'name',
      render: (_: unknown, r) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <CategoryTile category="tool" icon={compact ? Wrench : undefined} />
          <TwoLineCell
            primary={
              <>
                {r.name}
                {r.isSeed && showSeedBadge() ? (
                  <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                    seed
                  </Typography.Text>
                ) : null}
              </>
            }
            secondary={r.code}
          />
        </div>
      ),
    },
    { title: 'Total', dataIndex: 'totalUnits', width: 80, align: 'right' },
    { title: 'Available', dataIndex: 'availableCount', width: 90, align: 'right' },
    {
      title: 'Out',
      dataIndex: 'outCount',
      width: 72,
      align: 'right',
      render: (v: number) =>
        v > 0 ? <span style={{ fontWeight: 700, color: '#b45309' }}>{v}</span> : v,
    },
    ...(isPhone
      ? [
          {
            title: 'Stock',
            key: 'stock',
            render: (_: unknown, r: ToolType) => {
              if (!r.totalUnits) return <StatusPill color="gray" compact>No units</StatusPill>;
              if (!r.availableCount) return <StatusPill color="red" compact>None free</StatusPill>;
              return (
                <StatusPill color={r.outCount > 0 ? 'amber' : 'green'} compact>
                  {r.availableCount}/{r.totalUnits} free
                </StatusPill>
              );
            },
          },
        ]
      : []),
    {
      title: '',
      key: 'actions',
      width: 56,
      render: (_: unknown, r) => {
        if (!canEdit) return null;
        const items: MenuProps['items'] = [
          {
            key: 'add-unit',
            label: 'Add units',
            onClick: () => setUnitModalType(r),
          },
          {
            key: 'edit',
            label: 'Edit',
            onClick: () => openEditType(r),
          },
          {
            key: 'print-qr',
            icon: <PrinterOutlined />,
            label: 'Print QR labels',
            disabled: !r.totalUnits,
            onClick: () => void openLabelsForTypes([r.id]),
          },
          { type: 'divider' },
          { key: 'remove', label: 'Remove', danger: true, onClick: () => confirmRemoveType(r) },
        ];
        return (
          <Dropdown menu={{ items }} trigger={['click']}>
            <Button type="text" size="small" icon={<MoreOutlined />} aria-label="More" />
          </Dropdown>
        );
      },
    },
  ];

  const expandedRowRender = (record: ToolType) => {
    const units = unitsByType[record.id] || [];
    return (
      <ResponsiveTable
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={units}
        locale={{
          emptyText: 'No units for this type yet. Add a unit with its own asset code and QR.',
        }}
        columns={[
          {
            title: 'Asset code',
            dataIndex: 'assetCode',
            render: (c: string) => <span style={{ fontWeight: 600 }}>{c}</span>,
          },
          {
            title: 'Status',
            dataIndex: 'status',
            width: 110,
            render: (s: ToolUnitStatus) => statusPill(s),
          },
          {
            title: 'Holding',
            key: 'holder',
            render: (_: unknown, u: ToolUnit) =>
              u.status === 'OUT'
                ? `${u.currentHolderName || 'Worker'}${
                    u.heldSince ? ` · since ${formatShop(u.heldSince, 'MMM D')}` : ''
                  }`
                : '—',
          },
          {
            title: '',
            key: 'uactions',
            width: 120,
            render: (_: unknown, u: ToolUnit) => (
              <Space size={4}>
                <Button
                  size="small"
                  icon={<QrcodeOutlined />}
                  onClick={() => setQrUnit(u)}
                >
                  QR
                </Button>
                {canEdit && (
                <Dropdown menu={{ items: unitMenuItems(u) }} trigger={['click']}>
                  <Button size="small" type="text" icon={<MoreOutlined />} />
                </Dropdown>
                )}
              </Space>
            ),
          },
        ]}
      />
    );
  };

  const detail = detailType ? (types.find((t) => t.id === detailType.id) ?? detailType) : null;

  return (
    <div>
      <InventorySummaryCards cards={toolCards} activeKey={summaryKey} onSelect={setSummaryKey} />
      <div className="std-list-toolbar">
        <div className="std-list-filters">
          <Input
            allowClear
            placeholder="Search tool type…"
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="std-list-search"
          />
          <Select
            className="std-list-filter std-list-filter--sm"
            value={onlyOut ? 'out' : 'all'}
            onChange={(v) => setOnlyOut(v === 'out')}
            options={[
              { value: 'all', label: 'All types' },
              { value: 'out', label: 'With units out' },
            ]}
          />
        </div>
        <div className="std-list-actions">
          {!compact && showSeedBadge() && types.some((t) => t.isSeed) && (
            <InfoTip
              title="Seed placeholder tools"
              label="About seed tools"
              content="Types marked seed are temporary until the client provides the real asset list. Hidden in production builds."
            />
          )}
          {isPhone ? (
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  {
                    key: 'history',
                    icon: <SwapOutlined />,
                    label: 'Borrow history',
                    onClick: () => setHistoryOpen(true),
                  },
                  {
                    key: 'events',
                    icon: <HistoryOutlined />,
                    label: 'Event log',
                    onClick: () => setEventsOpen(true),
                  },
                  {
                    key: 'export',
                    icon: <DownloadOutlined />,
                    label: 'Export CSV',
                    onClick: () =>
                      exportCsv('tool-types.csv', filtered, [
                        { key: 'name', header: 'Name', value: (r: ToolType) => r.name },
                        { key: 'code', header: 'Code', value: (r: ToolType) => r.code },
                        { key: 'total', header: 'Total', value: (r: ToolType) => r.totalUnits },
                        { key: 'avail', header: 'Available', value: (r: ToolType) => r.availableCount },
                        { key: 'out', header: 'Out', value: (r: ToolType) => r.outCount },
                      ]),
                  },
                ],
              }}
            >
              <Button icon={<MoreOutlined />} aria-label="More actions" />
            </Dropdown>
          ) : (
            <>
              <Button icon={<SwapOutlined />} onClick={() => setHistoryOpen(true)}>
                Borrow history
              </Button>
              <Button icon={<HistoryOutlined />} onClick={() => setEventsOpen(true)}>
                Event log
              </Button>
              <Button
                icon={<DownloadOutlined />}
                onClick={() =>
                  exportCsv('tool-types.csv', filtered, [
                    { key: 'name', header: 'Name', value: (r: ToolType) => r.name },
                    { key: 'code', header: 'Code', value: (r: ToolType) => r.code },
                    { key: 'total', header: 'Total', value: (r: ToolType) => r.totalUnits },
                    { key: 'avail', header: 'Available', value: (r: ToolType) => r.availableCount },
                    { key: 'out', header: 'Out', value: (r: ToolType) => r.outCount },
                  ])
                }
              >
                Export CSV
              </Button>
            </>
          )}
          {canEdit && (
            <Button
              icon={<PrinterOutlined />}
              onClick={() => {
                setLabelPick(types.filter((t) => t.totalUnits > 0).map((t) => t.id));
                setLabelPickOpen(true);
              }}
            >
              {isPhone ? 'QR labels' : 'Print QR labels'}
            </Button>
          )}
          {canEdit && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              style={{ fontWeight: 700 }}
              onClick={() => setTypeModal(true)}
            >
              Add tool type
            </Button>
          )}
        </div>
      </div>

      <ResponsiveTable
        card={{ badge: 'stock', hide: ['totalUnits', 'availableCount', 'outCount'] }}
        className="std-list-table"
        size="small"
        rowKey="id"
        loading={loading}
        dataSource={filtered}
        columns={columns}
        expandable={
          compact
            ? undefined
            : {
                expandedRowKeys: expandedKeys,
                onExpand,
                expandedRowRender,
              }
        }
        onRow={compact ? (r) => rowOpenProps(() => openDetail(r)) : undefined}
        locale={{
          emptyText: onlyOut
            ? 'No tool types currently have units out with workers.'
            : 'No tool types yet. Add a type, then add individual units with QR codes.',
        }}
        pagination={false}
      />

      <Drawer
        title="Borrow and return history"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        size={720}
        destroyOnHidden
      >
        <Space style={{ marginBottom: 12 }} wrap>
          <DatePicker.RangePicker
            format="MMM D, YYYY"
            value={usageRange}
            allowClear={false}
            onChange={(vals) => {
              if (vals?.[0] && vals?.[1]) {
                setUsageRange([vals[0].startOf('day'), vals[1].endOf('day')]);
              }
            }}
          />
          <Button type="primary" loading={usageLoading} onClick={() => void fetchUsage()}>
            Refresh
          </Button>
        </Space>
        <ResponsiveTable
          className="std-list-table"
          size="small"
          rowKey={(r) => `${r.workerId}-${r.toolId}`}
          loading={usageLoading}
          dataSource={usage?.byWorkerItem || []}
          locale={{
            emptyText:
              'No borrow or return activity in this period. Workers scan a unit QR to create history.',
          }}
          columns={[
            { title: 'Worker', dataIndex: 'workerName' },
            {
              title: 'Unit',
              render: (_: unknown, r) =>
                [r.toolName, r.toolCode || r.assetCode].filter(Boolean).join(' · '),
            },
            { title: 'Borrowed', dataIndex: 'borrowQuantity', align: 'right', width: 90 },
            { title: 'Returned', dataIndex: 'returnQuantity', align: 'right', width: 90 },
          ]}
        />
      </Drawer>

      <Drawer
        open={Boolean(detailType)}
        onClose={() => setDetailType(null)}
        placement={isPhone ? 'bottom' : 'right'}
        size={isPhone ? 'auto' : 420}
        rootClassName={isPhone ? 'sched-f-drawer' : undefined}
        title={isPhone ? null : detailType?.name}
        closable={!isPhone}
        styles={isPhone ? { header: { display: 'none' } } : undefined}
        destroyOnHidden
      >
        {detail ? (
          <div className={isPhone ? 'sched-f tunits' : 'tunits'}>
            {isPhone ? (
              <>
                <div className="sched-f__handle" />
                <div className="sched-f__head">
                  <div>
                    <div className="sched-f__title">{detail.name}</div>
                    <div className="sched-f__sub">{detail.code}</div>
                  </div>
                  <button
                    type="button"
                    className="sched-m__icon"
                    onClick={() => setDetailType(null)}
                    aria-label="Close"
                  >
                    <CloseOutlined />
                  </button>
                </div>
              </>
            ) : null}
            <div className="tunits__summary">
              <span>
                <strong>{detail.availableCount}</strong> of {detail.totalUnits} available
              </span>
              {detail.outCount ? <span className="tunits__out">{detail.outCount} out</span> : null}
            </div>
            {!unitsByType[detail.id] ? (
              <Skeleton active title={false} paragraph={{ rows: 3 }} />
            ) : unitsByType[detail.id].length === 0 ? (
              <div className="tunits__empty">No units for this type yet. Add a unit with its own asset code and QR.</div>
            ) : (
              <ul className="tunits__list">
                {unitsByType[detail.id].map((u) => (
                  <li key={u.id} className="tunit">
                    <CategoryTile category="tool" icon={Wrench} size={32} />
                    <div className="tunit__main">
                      <div className="tunit__code">{u.assetCode}</div>
                      <div className="tunit__sub">
                        {u.status === 'OUT'
                          ? `With ${u.currentHolderName || 'a worker'}${
                              u.heldSince ? ` · since ${formatShop(u.heldSince, 'MMM D')}` : ''
                            }`
                          : u.status === 'AVAILABLE'
                            ? 'In the tool crib'
                            : null}
                      </div>
                    </div>
                    {statusPill(u.status)}
                    {canEdit ? (
                      <Dropdown menu={{ items: unitMenuItems(u) }} trigger={['click']}>
                        <Button type="text" icon={<MoreOutlined />} aria-label={`Change status of ${u.assetCode}`} />
                      </Dropdown>
                    ) : null}
                    <Button
                      className="tunit__qr"
                      icon={<QrcodeOutlined />}
                      aria-label={`Show QR for ${u.assetCode}`}
                      onClick={() => setQrUnit(u)}
                    />
                  </li>
                ))}
              </ul>
            )}
            {canEdit ? (
              <div className="tunits__actions">
                <Button icon={<PlusOutlined />} onClick={() => setUnitModalType(detailType)}>
                  Add units
                </Button>
                <Button onClick={() => detailType && openEditType(detailType)}>Edit</Button>
                <Button danger onClick={() => detailType && confirmRemoveType(detailType)}>
                  Remove
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </Drawer>

      <Drawer
        title="Tool event log"
        open={eventsOpen}
        onClose={() => setEventsOpen(false)}
        size={720}
        destroyOnHidden
      >
        <ToolEventsPage category="TOOL" />
      </Drawer>

      <Modal
        open={typeModal || Boolean(editingType)}
        onCancel={closeTypeModal}
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
            <AppstoreAddOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">{editingType ? 'Edit tool type' : 'Add tool type'}</div>
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={closeTypeModal}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <Form scrollToFirstError={{ block: 'center' }}
          form={typeForm}
          layout="vertical"
          onFinish={createType}
          style={{ padding: '20px 24px 8px' }}
        >
          {sectionLabel('Type details')}
          <Form.Item
            name="name"
            label="Name"
            rules={[{ required: true, message: 'Name is required' }]}
            style={{ marginBottom: 14 }}
          >
            <Input placeholder="e.g. Angle grinder" />
          </Form.Item>
          <Row gutter={12}>
            <Col xs={24} sm={12}>
              <Form.Item name="code" label="Type code" style={{ marginBottom: 14 }}>
                <Input placeholder="Auto-generated if empty" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="description" label="Description" style={{ marginBottom: 14 }}>
                <Input placeholder="Optional" />
              </Form.Item>
            </Col>
          </Row>
        </Form>
        <div className="app-form-modal__footer">
          <Button onClick={closeTypeModal} style={{ minWidth: 96 }}>
            Cancel
          </Button>
          <Button
            type="primary"
            loading={creatingType}
            onClick={() => typeForm.submit()}
            style={{ fontWeight: 700, minWidth: 120 }}
          >
            {editingType ? 'Save' : 'Create'}
          </Button>
        </div>
      </Modal>

      <Modal
        open={Boolean(unitModalType)}
        onCancel={() => setUnitModalType(null)}
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
            <AppstoreAddOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">Add units</div>
            {unitModalType ? <div className="app-form-modal__sub">{unitModalType.name}</div> : null}
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={() => setUnitModalType(null)}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <Form scrollToFirstError={{ block: 'center' }}
          form={unitForm}
          layout="vertical"
          onFinish={createUnit}
          style={{ padding: '20px 24px 8px' }}
        >
          {sectionLabel('Unit details')}
          <Form.Item
            name="quantity"
            label="How many units"
            initialValue={1}
            rules={[{ required: true, message: 'Enter how many units' }]}
            style={{ marginBottom: 14 }}
          >
            <InputNumber min={1} max={50} precision={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="assetCode"
            label="Asset code"
            extra={unitQty > 1 ? 'Several units are numbered for you, e.g. TYPE-004, TYPE-005.' : undefined}
            style={{ marginBottom: 14 }}
          >
            <Input placeholder="Auto-generated if empty (e.g. TYPE-001)" disabled={unitQty > 1} />
          </Form.Item>
          <Form.Item name="notes" label="Notes" style={{ marginBottom: 14 }}>
            <Input placeholder="Optional" />
          </Form.Item>
        </Form>
        <div className="app-form-modal__footer">
          <Button onClick={() => setUnitModalType(null)} style={{ minWidth: 96 }}>
            Cancel
          </Button>
          <Button
            type="primary"
            loading={creatingUnit}
            onClick={() => unitForm.submit()}
            style={{ fontWeight: 700, minWidth: 120 }}
          >
            Create
          </Button>
        </div>
      </Modal>

      <Modal
        open={Boolean(editUnit)}
        onCancel={() => setEditUnit(null)}
        footer={null}
        width={480}
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
            <AppstoreAddOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">Edit unit</div>
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={() => setEditUnit(null)}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <Form scrollToFirstError={{ block: 'center' }} form={editUnitForm} layout="vertical" onFinish={saveUnit} style={{ padding: '20px 24px 8px' }}>
          <Form.Item
            name="assetCode"
            label="Asset code"
            rules={[{ required: true, whitespace: true, message: 'Asset code is required' }]}
            style={{ marginBottom: 14 }}
          >
            <Input />
          </Form.Item>
          <Form.Item name="notes" label="Notes" style={{ marginBottom: 14 }}>
            <Input placeholder="Optional" />
          </Form.Item>
        </Form>
        <div className="app-form-modal__footer">
          <Button onClick={() => setEditUnit(null)} style={{ minWidth: 96 }}>
            Cancel
          </Button>
          <Button
            type="primary"
            loading={savingUnit}
            onClick={() => editUnitForm.submit()}
            style={{ fontWeight: 700, minWidth: 120 }}
          >
            Save
          </Button>
        </div>
      </Modal>

      <Modal
        title={qrUnit ? `QR: ${qrUnit.assetCode}` : 'QR'}
        open={Boolean(qrUnit)}
        onCancel={() => setQrUnit(null)}
        footer={
          qrUnit && canEdit
            ? [
                <Button key="png" icon={<DownloadOutlined />} onClick={() => void saveUnitPng(qrUnit)}>
                  Download PNG
                </Button>,
                <Button
                  key="print"
                  type="primary"
                  icon={<PrinterOutlined />}
                  onClick={() => {
                    setLabelItems([unitLabel(qrUnit, types.find((t) => t.id === qrUnit.toolTypeId))]);
                    setQrUnit(null);
                  }}
                >
                  Print label
                </Button>,
              ]
            : null
        }
      >
        {qrUnit && <UnitQrImage unitId={qrUnit.id} code={qrUnit.assetCode} />}
      </Modal>

      <FormModal
        title="Print QR labels"
        icon={<QrcodeOutlined />}
        size="sm"
        open={labelPickOpen}
        onCancel={() => setLabelPickOpen(false)}
        okText="Next"
        okButtonProps={{ disabled: !labelPick.length }}
        confirmLoading={labelLoading}
        onOk={() => void openLabelsForTypes(labelPick)}
        destroyOnHidden
      >
        <div style={{ marginBottom: 8 }}>
          Each unit of the chosen types gets a label.
        </div>
        <Select
          mode="multiple"
          allowClear
          style={{ width: '100%' }}
          placeholder="Tool types"
          value={labelPick}
          onChange={setLabelPick}
          optionFilterProp="label"
          maxTagCount="responsive"
          options={types
            .filter((t) => t.totalUnits > 0)
            .map((t) => ({ value: t.id, label: `${t.name} (${t.totalUnits})` }))}
        />
      </FormModal>

      <QrLabelPrintModal
        open={Boolean(labelItems)}
        items={labelItems || []}
        onClose={() => setLabelItems(null)}
      />
    </div>
  );
}

function UnitQrImage({ unitId, code }: { unitId: string; code: string }) {
  const [src, setSrc] = useState('');
  useEffect(() => {
    apiClient
      .get(`/tools/units/${unitId}/qr`, { responseType: 'blob' })
      .then(({ data }) => setSrc(URL.createObjectURL(data)));
  }, [unitId]);
  return (
    <div style={{ textAlign: 'center' }}>
      {src && <img src={src} alt={`QR ${code}`} style={{ maxWidth: '100%' }} />}
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
        {code}
      </Typography.Text>
    </div>
  );
}
