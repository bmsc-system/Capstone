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
  CalendarOutlined,
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
  CoverOutcome,
  WorkerAbsence,
} from '../../types';
import { confirmAction } from '../../utils/confirmAction';
import { DialogField, DialogFields } from '../../utils/DialogField';
import { newestFirst } from '../../utils/newestFirst';

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

const COVER_TEXT: Record<CoverOutcome['outcome'], (r: CoverOutcome) => string> = {
  REASSIGNED: (r) => `given to ${r.to ?? 'another worker'} at the same time`,
  HANDED_OVER: (r) => `handed over to ${r.to ?? 'another worker'}, who can resume it`,
  HELPER_REPLACED: (r) => `${r.to ?? 'Another worker'} helps instead`,
  HELPER_REMOVED: () => 'goes on without this helper (nobody else was free)',
  MOVED_LATER: (r) =>
    `moved later${r.newStart ? ` to ${formatShop(r.newStart, 'MMM D, h:mm A')}` : ''}; nobody else was free`,
  NEEDS_REPLAN: () => 'could not be moved automatically. Open the job and reschedule it.',
  LEFT_PAUSED: () => 'left paused; nobody is free to take it. Hand it over from the job when someone is.',
  UNCHANGED: () => 'kept as it is',
};

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
  // Workers marked away (absent, on leave, emergency) on the day shown.
  const [absences, setAbsences] = useState<WorkerAbsence[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [form] = Form.useForm();

  const dayKey = day.format('YYYY-MM-DD');

  const loadDay = useCallback(async () => {
    setLoading(true);
    try {
      const [{ data }, { data: away }] = await Promise.all([
        attendanceApi.day(dayKey),
        attendanceApi.absences(dayKey),
      ]);
      setSheet(data);
      setAbsences(away);
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
  // The latest entry on top: who clocked in last first, then everyone not in yet.
  const sheetRows = useMemo(
    () => newestFirst(sheet?.rows || [], (r) => r.record?.clockIn),
    [sheet?.rows],
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
      icon: <DeleteOutlined />,
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
  const isPast = (d: string) => dayjs(d).isBefore(shopToday(), 'day');
  const awayOn = (row: AttendanceRow) =>
    absences.find(
      (a) =>
        a.workerId === row.workerId &&
        formatShop(a.startsAt, 'YYYY-MM-DD') <= row.date &&
        formatShop(a.endsAt, 'YYYY-MM-DD') >= row.date
    );
  /** Leave entered ahead of time that has not started on the day shown. */
  const comingAway = (row: AttendanceRow) =>
    absences.find((a) => a.workerId === row.workerId && formatShop(a.startsAt, 'YYYY-MM-DD') > row.date);

  /** Mark a worker away: their work in that period is handed to others or moved. */
  const markAway = (row: AttendanceRow) => {
    let reason: WorkerAbsence['reason'] = 'ABSENT';
    let firstDay: Dayjs = dayjs(row.date);
    let lastDay: Dayjs = dayjs(row.date);
    let note = '';
    confirmAction({
      wide: true,
      icon: <CalendarOutlined />,
      title: `Mark ${row.workerName} as away?`,
      content: (
        <DialogFields intro="Their work in this period is covered for you: given to a free worker with the skill, or moved to after they are back. You see what changed and can adjust it.">
          <DialogField label="Reason">
            <Select
              style={{ width: '100%' }}
              defaultValue="ABSENT"
              onChange={(v: WorkerAbsence['reason']) => {
                reason = v;
              }}
              options={[
                { value: 'ABSENT', label: 'Absent' },
                { value: 'EMERGENCY', label: 'Emergency (left during the day)' },
                { value: 'LEAVE', label: 'On leave' },
              ]}
            />
          </DialogField>
          <div className="app-dialog-row">
            <DialogField label="First day away">
              <DatePicker
                style={{ width: '100%' }}
                defaultValue={dayjs(row.date)}
                format="MMM D, YYYY"
                allowClear={false}
                disabledDate={(d) => d.isBefore(shopToday(), 'day')}
                onChange={(d) => {
                  if (d) firstDay = d;
                }}
              />
            </DialogField>
            <DialogField label="Last day away">
              <DatePicker
                style={{ width: '100%' }}
                defaultValue={dayjs(row.date)}
                format="MMM D, YYYY"
                allowClear={false}
                disabledDate={(d) => d.isBefore(shopToday(), 'day')}
                onChange={(d) => {
                  if (d) lastDay = d;
                }}
              />
            </DialogField>
          </div>
          <DialogField label="Note" optional>
            <Input
              maxLength={255}
              placeholder="For example: family emergency"
              onChange={(e) => {
                note = e.target.value;
              }}
            />
          </DialogField>
        </DialogFields>
      ),
      okText: 'Mark away',
      onOk: async () => {
        try {
          const { data } = await attendanceApi.markAway({
            workerId: row.workerId,
            fromDate: firstDay.format('YYYY-MM-DD'),
            toDate: (lastDay.isBefore(firstDay, 'day') ? firstDay : lastDay).format('YYYY-MM-DD'),
            reason,
            note: note.trim() || undefined,
          });
          const rows = data.cover.operations;
          Modal.info({
            title: `${row.workerName} is marked away`,
            width: 560,
            content: rows.length ? (
              <ul style={{ paddingLeft: 18, margin: '8px 0 0' }}>
                {rows.map((r) => (
                  <li key={r.operationId} style={{ marginBottom: 4 }}>
                    <strong>{r.label}</strong>: {COVER_TEXT[r.outcome](r)}
                  </li>
                ))}
              </ul>
            ) : (
              'They had no scheduled work in that period.'
            ),
          });
          await loadDay();
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const markBack = (a: WorkerAbsence) =>
    confirmAction({
      icon: <CalendarOutlined />,
      title: `${a.workerName ?? 'This worker'} is not away after all?`,
      content: 'They can be scheduled again in that period. Work already handed to others stays with them.',
      okText: 'Yes, remove it',
      onOk: async () => {
        try {
          await attendanceApi.cancelAbsence(a.id);
          message.success('Away period removed');
          await loadDay();
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });

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
        {awayOn(row) ? (
          <Button size="small" type="text" onClick={() => markBack(awayOn(row)!)}>
            Back
          </Button>
        ) : comingAway(row) ? (
          <Button size="small" type="text" onClick={() => markBack(comingAway(row)!)}>
            Cancel leave
          </Button>
        ) : !isPast(row.date) && !(rec && rec.clockOut) ? (
          <Button size="small" type="text" onClick={() => markAway(row)}>
            Mark away
          </Button>
        ) : null}
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
    {
      title: 'Status',
      key: 'status',
      width: 240,
      render: (_: unknown, r) => {
        const away = awayOn(r);
        return (
          <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
            <StatusCell row={r} />
            {away ? (
              <StatusPill tone="amber" compact>
                Away: {away.reasonLabel}
              </StatusPill>
            ) : comingAway(r) ? (
              <StatusPill tone="slate" compact>
                {comingAway(r)!.reasonLabel} from {formatShop(comingAway(r)!.startsAt, 'MMM D')}
              </StatusPill>
            ) : null}
          </span>
        );
      },
    },
    {
      title: 'Note',
      key: 'note',
      ellipsis: true,
      render: (_: unknown, r) => r.record?.note || '',
    },
    {
      title: '',
      key: 'actions',
      width: 240,
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
            dataSource={sheetRows}
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
