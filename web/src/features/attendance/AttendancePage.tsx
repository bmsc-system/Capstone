import { formatShop, isoToShopDayjs, shopLocalToIso, shopToday } from '../../utils/shopTime';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  DatePicker,
  Dropdown,
  Form,
  Alert,
  Input,
  Modal,
  Select,
  TimePicker,
  message,
} from 'antd';
import type { TableColumnsType } from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CoffeeOutlined,
  DeleteOutlined,
  FieldTimeOutlined,
  DownloadOutlined,
  EditOutlined,
  LeftOutlined,
  LoginOutlined,
  LogoutOutlined,
  MoreOutlined,
  RightOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import { attendanceApi } from '../../api/attendance.api';
import { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import { DataTable, EmptyState, StatCard, StatGrid, TabBar } from '../../components/ui';
import { ATTENDANCE_STATUS } from '../../theme/statuses';
import { downloadCsv } from '../../utils/csvExport';
import type {
  AttendanceDaySheet,
  AttendanceHistory,
  AttendanceRow,
} from '../../types';
import { confirmAction } from '../../utils/confirmAction';

type ModalMode = 'in' | 'out' | 'edit';

function fmtTime(iso: string | null | undefined) {
  return iso ? formatShop(iso, 'h:mm A') : '—';
}

function fmtHours(h: number | null | undefined) {
  if (h == null) return '—';
  const whole = Math.floor(h);
  const mins = Math.round((h - whole) * 60);
  return mins ? `${whole}h ${mins}m` : `${whole}h`;
}

function fmtShift(r: AttendanceRow) {
  if (!r.isWorkingDay || !r.scheduledStart || !r.scheduledEnd) return 'Day off';
  const t = (s: string) => dayjs(`2000-01-01T${s}`).format('h:mm A');
  return `${t(r.scheduledStart)} – ${t(r.scheduledEnd)}`;
}

function atTime(day: string, t: Dayjs) {
  return dayjs(day).hour(t.hour()).minute(t.minute()).second(0).millisecond(0);
}

function shopWallNow(): Dayjs {
  return isoToShopDayjs(new Date().toISOString()) ?? shopToday();
}

function defaultTime(day: string, scheduled: string | null, fallback: string) {
  if (dayjs(day).isSame(shopToday(), 'day')) return shopWallNow().second(0);
  return dayjs(`2000-01-01T${scheduled || fallback}`);
}

function StatusCell({ row }: { row: AttendanceRow }) {
  return (
    <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
      <StatusPill status={row.status} map={ATTENDANCE_STATUS} compact />
      {row.punctuality === 'OUTSIDE' && (
        <StatusPill tone="slate" compact>
          Outside shift
        </StatusPill>
      )}
      {row.punctuality === 'OVERTIME' && (
        <StatusPill tone={ATTENDANCE_STATUS.LATE.tone} compact>
          Overtime
        </StatusPill>
      )}
      {row.punctuality === 'LATE' && row.lateMinutes > 0 && (
        <StatusPill tone={ATTENDANCE_STATUS.LATE.tone} compact>
          Late {fmtHours(row.lateMinutes / 60)}
        </StatusPill>
      )}
    </span>
  );
}

export default function AttendancePage() {
  const [view, setView] = useState<'day' | 'history'>('day');
  const [day, setDay] = useState<Dayjs>(shopToday());
  const [sheet, setSheet] = useState<AttendanceDaySheet | null>(null);
  const [loading, setLoading] = useState(true);

  const [historyWorker, setHistoryWorker] = useState<string | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs]>([shopToday().startOf('month'), shopToday()]);
  const [history, setHistory] = useState<AttendanceHistory | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [modal, setModal] = useState<{ mode: ModalMode; row: AttendanceRow } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [form] = Form.useForm();

  const dayKey = day.format('YYYY-MM-DD');

  const loadDay = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await attendanceApi.day(dayKey);
      setSheet(data);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [dayKey]);

  const loadHistory = useCallback(async () => {
    if (!historyWorker) {
      setHistory(null);
      return;
    }
    setHistoryLoading(true);
    try {
      const { data } = await attendanceApi.history(historyWorker, {
        from: range[0].format('YYYY-MM-DD'),
        to: range[1].format('YYYY-MM-DD'),
      });
      setHistory(data);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setHistoryLoading(false);
    }
  }, [historyWorker, range]);

  useEffect(() => {
    void loadDay();
  }, [loadDay]);

  useEffect(() => {
    if (view === 'history') void loadHistory();
  }, [view, loadHistory]);

  const workerOptions = useMemo(
    () => (sheet?.rows || []).map((r) => ({ value: r.workerId, label: r.workerName })),
    [sheet]
  );

  useEffect(() => {
    if (view === 'history' && !historyWorker && workerOptions.length) {
      setHistoryWorker(workerOptions[0].value);
    }
  }, [view, historyWorker, workerOptions]);

  const openModal = (mode: ModalMode, row: AttendanceRow) => {
    setSaveError('');
    const rec = row.record;
    form.resetFields();
    form.setFieldsValue({
      clockIn: rec ? isoToShopDayjs(rec.clockIn) : defaultTime(row.date, row.scheduledStart, '08:00'),
      clockOut: rec?.clockOut
        ? isoToShopDayjs(rec.clockOut)
        : mode === 'out'
          ? defaultTime(row.date, row.scheduledEnd, '17:00')
          : null,
      note: rec?.note || '',
    });
    setModal({ mode, row });
  };

  const refresh = async () => {
    await loadDay();
    if (view === 'history') await loadHistory();
  };

  const onSave = async (values: { clockIn?: Dayjs; clockOut?: Dayjs | null; note?: string }) => {
    if (!modal) return;
    const { mode, row } = modal;
    const rec = row.record;
    const inAt = values.clockIn ? atTime(row.date, values.clockIn) : rec ? isoToShopDayjs(rec.clockIn) : null;
    let outAt = values.clockOut ? atTime(row.date, values.clockOut) : null;
    if (inAt && outAt && outAt.isBefore(inAt) && !outAt.add(1, 'day').isAfter(shopWallNow())) {
      outAt = outAt.add(1, 'day');
    }
    try {
      setSaving(true);
      setSaveError('');
      if (mode === 'in' && inAt) {
        await attendanceApi.clockIn({
          workerId: row.workerId,
          clockIn: shopLocalToIso(inAt)!,
          note: values.note || undefined,
        });
        message.success(`${row.workerName} clocked in at ${inAt.format('h:mm A')}`);
      } else if (mode === 'out' && rec && outAt) {
        await attendanceApi.clockOut(rec.id, { clockOut: shopLocalToIso(outAt)! });
        message.success(`${row.workerName} clocked out at ${outAt.format('h:mm A')}`);
      } else if (mode === 'edit' && rec && inAt) {
        await attendanceApi.update(rec.id, {
          clockIn: shopLocalToIso(inAt)!,
          clockOut: outAt ? shopLocalToIso(outAt) : null,
          note: values.note || null,
        });
        message.success('Attendance updated');
      }
      setModal(null);
      await refresh();
    } catch (err) {
      setSaveError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = (row: AttendanceRow) => {
    if (!row.record) return;
    const id = row.record.id;
    confirmAction({
      title: `Delete ${row.workerName}'s attendance for ${formatShop(row.date, 'MMM D, YYYY')}?`,
      content: 'Only for a record entered by mistake.',
      okText: 'Delete',
      danger: true,
      onOk: async () => {
        try {
          await attendanceApi.remove(id);
          message.success('Attendance deleted');
          await refresh();
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });
  };

  const [exporting, setExporting] = useState(false);
  const exportCurrent = async () => {
    setExporting(true);
    try {
      if (view === 'day') {
        const { data } = await attendanceApi.dayCsv(dayKey);
        downloadCsv(`attendance-${dayKey}.csv`, data);
      } else if (historyWorker) {
        const from = range[0].format('YYYY-MM-DD');
        const to = range[1].format('YYYY-MM-DD');
        const { data } = await attendanceApi.historyCsv(historyWorker, { from, to });
        const name = (history?.workerName || 'worker').toLowerCase().split(/\s+/).join('-');
        downloadCsv(`attendance-${name}-${from}_${to}.csv`, data);
      }
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setExporting(false);
    }
  };

  const isFuture = (d: string) => dayjs(d).isAfter(shopToday(), 'day');

  const actionsCell = (row: AttendanceRow) => {
    const rec = row.record;
    return (
      <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
        {!rec && !isFuture(row.date) && (
          <Button
            size="small"
            type={row.status === 'OFF' ? 'text' : 'default'}
            icon={<LoginOutlined />}
            onClick={() => openModal('in', row)}
          >
            Clock in
          </Button>
        )}
        {rec && !rec.clockOut && (
          <Button size="small" icon={<LogoutOutlined />} onClick={() => openModal('out', row)}>
            Clock out
          </Button>
        )}
        {rec && (
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                { key: 'edit', icon: <EditOutlined />, label: 'Edit times', onClick: () => openModal('edit', row) },
                {
                  key: 'delete',
                  icon: <DeleteOutlined />,
                  danger: true,
                  label: 'Delete',
                  onClick: () => confirmDelete(row),
                },
              ],
            }}
          >
            <Button size="small" type="text" icon={<MoreOutlined />} aria-label="More actions" />
          </Dropdown>
        )}
      </span>
    );
  };

  const dayColumns: TableColumnsType<AttendanceRow> = [
    {
      title: 'Worker',
      dataIndex: 'workerName',
      width: 200,
      ellipsis: true,
      render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span>,
    },
    { title: 'Scheduled', key: 'shift', width: 170, render: (_: unknown, r) => fmtShift(r) },
    { title: 'Clock in', key: 'in', width: 100, render: (_: unknown, r) => fmtTime(r.record?.clockIn) },
    { title: 'Clock out', key: 'out', width: 100, render: (_: unknown, r) => fmtTime(r.record?.clockOut) },
    {
      title: 'Hours present',
      key: 'hours',
      width: 120,
      render: (_: unknown, r) => fmtHours(r.record?.hoursPresent),
    },
    {
      title: 'Hours worked',
      key: 'worked',
      width: 120,
      render: (_: unknown, r) => fmtHours(r.record?.hoursWorked),
    },
    { title: 'Status', key: 'status', width: 220, render: (_: unknown, r) => <StatusCell row={r} /> },
    {
      title: 'Note',
      key: 'note',
      ellipsis: true,
      render: (_: unknown, r) => r.record?.note || '',
    },
    {
      title: '',
      key: 'actions',
      width: 150,
      align: 'right',
      fixed: 'right',
      render: (_: unknown, r) => actionsCell(r),
    },
  ];

  const historyColumns: TableColumnsType<AttendanceRow> = [
    {
      title: 'Date',
      dataIndex: 'date',
      width: 150,
      render: (v: string) => formatShop(v, 'ddd, MMM D, YYYY'),
    },
    ...dayColumns.slice(1),
  ];

  const counts = sheet?.counts || {};
  const isToday = day.isSame(shopToday(), 'day');

  return (
    <div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          marginBottom: 16,
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <TabBar<'day' | 'history'>
          value={view}
          onChange={setView}
          items={[
            { label: 'Daily sheet', value: 'day' },
            { label: 'Worker history', value: 'history' },
          ]}
        />
        {view === 'day' ? (
          <div className="att-toolbar" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Button className="ui-iconbtn" icon={<LeftOutlined />} onClick={() => setDay(day.subtract(1, 'day'))} aria-label="Previous day" />
            <DatePicker
              value={day}
              allowClear={false}
              format="ddd, MMM D, YYYY"
              disabledDate={(d) => d.isAfter(shopToday(), 'day')}
              onChange={(d) => d && setDay(d)}
            />
            <Button
              className="ui-iconbtn"
              icon={<RightOutlined />}
              disabled={isToday}
              onClick={() => setDay(day.add(1, 'day'))}
              aria-label="Next day"
            />
            {!isToday && <Button onClick={() => setDay(shopToday())}>Today</Button>}
            <Button
              icon={<DownloadOutlined />}
              loading={exporting}
              disabled={!sheet?.rows.length}
              onClick={exportCurrent}
            >
              Export CSV
            </Button>
          </div>
        ) : (
          <div className="att-toolbar att-toolbar--history" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="Select a worker"
              value={historyWorker}
              onChange={setHistoryWorker}
              options={workerOptions}
              style={{ width: 220 }}
            />
            <DatePicker.RangePicker
              format="MMM D, YYYY"
              value={range}
              allowClear={false}
              disabledDate={(d) => d.isAfter(shopToday(), 'day')}
              onChange={(v) => v?.[0] && v?.[1] && setRange([v[0], v[1]])}
            />
            <Button
              icon={<DownloadOutlined />}
              loading={exporting}
              disabled={!history?.rows.length}
              onClick={exportCurrent}
            >
              Export CSV
            </Button>
          </div>
        )}
      </div>

      {view === 'day' ? (
        <>
          <div style={{ marginBottom: 16 }}>
            <StatGrid min={170}>
              <StatCard
                icon={<CheckCircleOutlined />}
                tone="green"
                label="Present"
                value={(counts.PRESENT || 0) + (counts.CLOCKED_IN || 0) + (counts.INCOMPLETE || 0)}
                loading={loading && !sheet}
              />
              {isToday ? (
                <StatCard
                  icon={<LoginOutlined />}
                  tone="blue"
                  label="Still clocked in"
                  value={counts.CLOCKED_IN || 0}
                  loading={loading && !sheet}
                />
              ) : (
                <StatCard
                  icon={<FieldTimeOutlined />}
                  tone="amber"
                  label="Incomplete"
                  value={counts.INCOMPLETE || 0}
                  loading={loading && !sheet}
                />
              )}
              <StatCard
                icon={<ClockCircleOutlined />}
                tone="amber"
                label="Late"
                value={sheet?.lateCount || 0}
                loading={loading && !sheet}
              />
              <StatCard
                icon={<CloseCircleOutlined />}
                tone="red"
                label={isToday ? 'Absent / not in yet' : 'Absent'}
                value={(counts.ABSENT || 0) + (counts.NOT_IN || 0)}
                loading={loading && !sheet}
              />
              <StatCard
                icon={<CoffeeOutlined />}
                tone="grey"
                label="Day off"
                value={counts.OFF || 0}
                loading={loading && !sheet}
              />
            </StatGrid>
          </div>
          <DataTable<AttendanceRow>
            card={{ subtitle: ['shift'], badge: 'status', full: ['note'] }}
            cardsBelow="narrow"
            rowKey="workerId"
            loading={loading}
            dataSource={sheet?.rows || []}
            columns={dayColumns}
            pagination={false}
            scroll={{ x: 1180 }}
          />
        </>
      ) : !historyWorker ? (
        <EmptyState icon={<ClockCircleOutlined />} title="Select a worker to see their attendance." />
      ) : (
        <>
          <div style={{ marginBottom: 16 }}>
            <StatGrid min={170}>
              <StatCard icon={<CheckCircleOutlined />} tone="green" label="Days present" value={history?.summary.daysPresent ?? 0} />
              <StatCard icon={<CloseCircleOutlined />} tone="red" label="Days absent" value={history?.summary.daysAbsent ?? 0} />
              <StatCard icon={<ClockCircleOutlined />} tone="amber" label="Days late" value={history?.summary.daysLate ?? 0} />
              <StatCard icon={<FieldTimeOutlined />} tone="blue" label="Hours present" value={fmtHours(history?.summary.hoursPresent ?? 0)} />
              <StatCard icon={<FieldTimeOutlined />} tone="indigo" label="Hours worked" value={fmtHours(history?.summary.hoursWorked ?? 0)} />
            </StatGrid>
          </div>
          <DataTable<AttendanceRow>
            card={{ subtitle: ['shift'], badge: 'status', full: ['note'] }}
            cardsBelow="narrow"
            rowKey="date"
            loading={historyLoading}
            dataSource={history?.rows || []}
            columns={historyColumns}
            pagination={{ pageSize: 31 }}
            scroll={{ x: 1120 }}
          />
        </>
      )}

      <Modal
        open={!!modal}
        onCancel={() => setModal(null)}
        footer={null}
        width={460}
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
            <ClockCircleOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">
              {modal?.mode === 'in' ? 'Clock in' : modal?.mode === 'out' ? 'Clock out' : 'Edit attendance'}
            </div>
            <div className="app-form-modal__sub">
              {modal?.row.workerName} · {modal ? formatShop(modal.row.date, 'ddd, MMM D, YYYY') : ''}
              {modal && modal.row.isWorkingDay ? ` · Scheduled ${fmtShift(modal.row)}` : ''}
            </div>
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={() => setModal(null)}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <Form scrollToFirstError={{ block: 'center' }} form={form} layout="vertical" onFinish={onSave} style={{ padding: '20px 24px 8px' }}>
          {saveError ? (
            <Alert type="error" showIcon title={saveError} style={{ marginBottom: 12 }} />
          ) : null}
          <div style={{ display: 'flex', gap: 16 }}>
            {modal?.mode !== 'out' && (
              <Form.Item
                name="clockIn"
                label="Clock-in time"
                rules={[{ required: true, message: 'Enter the clock-in time' }]}
                style={{ flex: 1 }}
              >
                <TimePicker format="h:mm A" use12Hours style={{ width: '100%' }} />
              </Form.Item>
            )}
            {modal?.mode !== 'in' && (
              <Form.Item
                name="clockOut"
                label="Clock-out time"
                rules={modal?.mode === 'out' ? [{ required: true, message: 'Enter the clock-out time' }] : []}
                tooltip="A time earlier than clock-in is counted as the next day (overnight shift)."
                style={{ flex: 1 }}
              >
                <TimePicker
                  format="h:mm A"
                  use12Hours
                  style={{ width: '100%' }}
                  allowClear={modal?.mode === 'edit'}
                />
              </Form.Item>
            )}
          </div>
          {modal?.mode !== 'out' && (
            <Form.Item name="note" label="Note (optional)">
              <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder="e.g. Half day, came in late due to traffic" />
            </Form.Item>
          )}
        </Form>
        <div className="app-form-modal__footer">
          <Button onClick={() => setModal(null)} style={{ minWidth: 96 }}>
            Cancel
          </Button>
          <Button
            type="primary"
            loading={saving}
            onClick={() => form.submit()}
            style={{ fontWeight: 700, minWidth: 120 }}
          >
            Save
          </Button>
        </div>
      </Modal>
    </div>
  );
}
