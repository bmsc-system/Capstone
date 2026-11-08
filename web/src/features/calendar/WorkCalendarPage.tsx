import { formatShop, isPastShopDay, shopNow, shopToday } from '../../utils/shopTime';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  TimePicker,
  message,
  DatePicker,
} from 'antd';
import { CalendarOutlined, DeleteOutlined, ClockCircleOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  calendarApi,
  type CalendarAffectedJob,
  type CalendarExceptionType,
  type ShopBreak,
  type WorkCalendarException,
} from '../../api/calendar.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import PeriodNavigator from '../../components/PeriodNavigator';
import RescheduleAffectedJobs from './RescheduleAffectedJobs';
import { Legend, PageSkeleton, FormModal } from '../../components/ui';
import './workCalendar.css';
import { confirmAction } from '../../utils/confirmAction';

const TYPE_LABEL: Record<CalendarExceptionType, string> = {
  OVERTIME: 'Overtime',
  SPECIAL_WORKING_DAY: 'Special working day',
  HOLIDAY_NO_WORK: 'Holiday',
};

function dateRangeLabel(from: string, to: string): string {
  const a = dayjs(from);
  const b = dayjs(to);
  return from === to
    ? a.format('MMM D, YYYY')
    : `${a.format('MMM D')} – ${b.format('MMM D, YYYY')}`;
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function sectionLabel(text: string) {
  return <div className="app-form-section">{text}</div>;
}

function isNormalWorkingDay(d: Dayjs): boolean {
  const dow = d.day(); // 0=Sun
  return dow >= 1 && dow <= 6;
}

function exceptionBadge(exc: WorkCalendarException): string {
  if (exc.type === 'HOLIDAY_NO_WORK') return 'Closed';
  if (exc.type === 'OVERTIME') {
    const until = exc.endTime ? exc.endTime.slice(0, 5) : '—';
    return `OT until ${until}`;
  }
  const start = exc.startTime ? exc.startTime.slice(0, 5) : '08:00';
  const end = exc.endTime ? exc.endTime.slice(0, 5) : '17:00';
  const sunday = dayjs(exc.date).day() === 0;
  return sunday ? `Sunday work, ${start}–${end}` : `Special, ${start}–${end}`;
}

function defaultsForType(type: CalendarExceptionType): {
  startTime: Dayjs | null;
  endTime: Dayjs | null;
} {
  if (type === 'HOLIDAY_NO_WORK') return { startTime: null, endTime: null };
  if (type === 'OVERTIME') {
    return {
      startTime: dayjs('17:00', 'HH:mm'),
      endTime: dayjs('20:00', 'HH:mm'),
    };
  }
  return {
    startTime: dayjs('08:00', 'HH:mm'),
    endTime: dayjs('17:00', 'HH:mm'),
  };
}

function excTitle(exc: WorkCalendarException): string {
  if (exc.type === 'HOLIDAY_NO_WORK') return 'Holiday';
  if (exc.type === 'OVERTIME') return 'Overtime';
  return dayjs(exc.date).day() === 0 ? 'Sunday work' : 'Special day';
}

function excDetail(exc: WorkCalendarException): string {
  if (exc.type === 'HOLIDAY_NO_WORK') return exc.note || 'Closed';
  const start = exc.startTime?.slice(0, 5);
  const end = exc.endTime?.slice(0, 5);
  if (exc.type === 'OVERTIME') return start && end ? `${start}–${end}` : `until ${end ?? '—'}`;
  return `${start ?? '08:00'}–${end ?? '17:00'}`;
}

/** Monday-first grid holding only the weeks the month touches (5 or 6, sometimes 4). */
function monthCells(anchor: Dayjs): Dayjs[] {
  const start = anchor.startOf('month');
  const lead = (start.day() + 6) % 7;
  const gridStart = start.subtract(lead, 'day');
  const weeks = Math.ceil((lead + anchor.daysInMonth()) / 7);
  return Array.from({ length: weeks * 7 }, (_, i) => gridStart.add(i, 'day'));
}

const MIN_CALENDAR_HEIGHT = 520;

/**
 * Height that makes the element end exactly at the bottom of the app's scroll
 * area, keeping whatever padding sits below it.
 */
function useFitToScrollArea(deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);

  useLayoutEffect(() => {
    const el = ref.current;
    const scroller = el?.closest('.app-shell__scroll') as HTMLElement | null;
    if (!el || !scroller) return;
    const fit = () => {
      // Hidden kept-alive page (display:none ancestor): nothing to measure.
      if (el.offsetParent === null) return;
      const top =
        el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      let below = parseFloat(getComputedStyle(el).marginBottom) || 0;
      for (let node = el.parentElement; node && node !== scroller; node = node.parentElement) {
        const cs = getComputedStyle(node);
        below +=
          (parseFloat(cs.paddingBottom) || 0) +
          (parseFloat(cs.borderBottomWidth) || 0) +
          (parseFloat(cs.marginBottom) || 0);
      }
      const next = Math.floor(scroller.clientHeight - top - below);
      setHeight(Math.max(MIN_CALENDAR_HEIGHT, next));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(scroller);
    window.addEventListener('resize', fit);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', fit);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { ref, height };
}

export default function WorkCalendarPage() {
  const { isAdmin } = useAuth();
  const [anchor, setAnchor] = useState(() => shopToday());
  const [exceptions, setExceptions] = useState<WorkCalendarException[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<WorkCalendarException | null>(null);
  const [saving, setSaving] = useState(false);
  const [affected, setAffected] = useState<{
    changeLabel: string;
    jobs: CalendarAffectedJob[];
  } | null>(null);
  const [form] = Form.useForm();
  const watchType = Form.useWatch('type', form) as CalendarExceptionType | undefined;
  const [shopBreak, setShopBreak] = useState<ShopBreak | null>(null);
  const [breakOpen, setBreakOpen] = useState(false);
  const [breakSaving, setBreakSaving] = useState(false);
  const [breakForm] = Form.useForm();

  useEffect(() => {
    calendarApi
      .getBreak()
      .then(({ data }) => setShopBreak(data))
      .catch(() => setShopBreak(null));
  }, []);

  const openBreak = () => {
    if (!isAdmin || !shopBreak) return;
    breakForm.setFieldsValue({
      breakStart: dayjs(shopBreak.breakStart, 'HH:mm'),
      breakEnd: dayjs(shopBreak.breakEnd, 'HH:mm'),
    });
    setBreakOpen(true);
  };

  const saveBreak = async () => {
    const values = await breakForm.validateFields();
    setBreakSaving(true);
    try {
      const { data } = await calendarApi.setBreak({
        breakStart: (values.breakStart as Dayjs).format('HH:mm'),
        breakEnd: (values.breakEnd as Dayjs).format('HH:mm'),
      });
      setShopBreak(data);
      setBreakOpen(false);
      message.success('Break saved');
      if (data.affectedJobs.length) {
        setAffected({
          changeLabel: `Break changed to ${data.breakStart}–${data.breakEnd}`,
          jobs: data.affectedJobs,
        });
      }
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setBreakSaving(false);
    }
  };

  const byDate = useMemo(() => {
    const map = new Map<string, WorkCalendarException>();
    for (const e of exceptions) map.set(e.date, e);
    return map;
  }, [exceptions]);

  const fetchMonth = async (month: Dayjs) => {
    setLoading(true);
    try {
      const from = month.startOf('month').format('YYYY-MM-DD');
      const to = month.endOf('month').format('YYYY-MM-DD');
      const { data } = await calendarApi.list(from, to);
      setExceptions(data || []);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMonth(anchor);
  }, [anchor]);

  const openDay = (date: Dayjs) => {
    const existing = byDate.get(date.format('YYYY-MM-DD'));
    if (existing) {
      openEdit(existing);
      return;
    }
    if (!isAdmin) return;
    setEditing(null);
    const type: CalendarExceptionType = isNormalWorkingDay(date)
      ? 'OVERTIME'
      : 'SPECIAL_WORKING_DAY';
    const defaults = defaultsForType(type);
    form.setFieldsValue({
      type,
      date,
      dateTo: null,
      startTime: defaults.startTime,
      endTime: defaults.endTime,
      note: undefined,
    });
    setFormOpen(true);
  };

  const openEdit = (exc: WorkCalendarException) => {
    setEditing(exc);
    form.setFieldsValue({
      type: exc.type,
      date: dayjs(exc.date),
      dateTo: null,
      startTime: exc.startTime ? dayjs(exc.startTime, 'HH:mm') : null,
      endTime: exc.endTime ? dayjs(exc.endTime, 'HH:mm') : null,
      note: exc.note || undefined,
    });
    setFormOpen(true);
  };

  const offerReschedule = async (from: string, to: string, change: string) => {
    try {
      const { data } = await calendarApi.affectedJobs(from, to);
      if (data.jobs.length) {
        setAffected({ changeLabel: `${change} ${dateRangeLabel(from, to)}`, jobs: data.jobs });
      }
    } catch {
      /* advisory only; the calendar change itself already saved */
    }
  };

  const onTypeChange = (type: CalendarExceptionType) => {
    const defaults = defaultsForType(type);
    form.setFieldsValue({
      startTime: defaults.startTime,
      endTime: defaults.endTime,
    });
  };

  const submitForm = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const type = values.type as CalendarExceptionType;
      const payload = {
        type,
        date: (values.date as Dayjs).format('YYYY-MM-DD'),
        dateTo: values.dateTo
          ? (values.dateTo as Dayjs).format('YYYY-MM-DD')
          : undefined,
        startTime:
          type === 'HOLIDAY_NO_WORK'
            ? null
            : (values.startTime as Dayjs)?.format('HH:mm') || null,
        endTime:
          type === 'HOLIDAY_NO_WORK'
            ? null
            : (values.endTime as Dayjs)?.format('HH:mm') || null,
        note: values.note?.trim() || null,
      };

      let changed: { from: string; to: string; label: string };
      if (editing) {
        await calendarApi.update(editing.id, {
          type: payload.type,
          date: payload.date,
          startTime: payload.startTime,
          endTime: payload.endTime,
          note: payload.note,
        });
        message.success('Exception updated');
        const [from, to] = [editing.date, payload.date].sort();
        changed = { from, to, label: `${TYPE_LABEL[type]} changed on` };
      } else {
        const { data } = await calendarApi.create(payload);
        message.success(
          data.length > 1
            ? `Created ${data.length} calendar exceptions`
            : 'Exception created'
        );
        changed = {
          from: payload.date,
          to: payload.dateTo || payload.date,
          label: `${TYPE_LABEL[type]} added on`,
        };
      }
      setFormOpen(false);
      await fetchMonth(anchor);
      await offerReschedule(changed.from, changed.to, changed.label);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async (exc: WorkCalendarException) => {
    if (!isAdmin) return;
    let impactCount = 0;
    try {
      if (exc.type === 'OVERTIME' || exc.type === 'SPECIAL_WORKING_DAY') {
        const { data } = await calendarApi.deleteImpact(exc.id);
        impactCount = data.affectedCount || 0;
      }
    } catch {
      /* still allow delete; impact is advisory */
    }

    confirmAction({
      title: `Remove exception on ${formatShop(exc.date, 'MMM D, YYYY')}?`,
      content:
        impactCount > 0
          ? `${impactCount} scheduled operation${impactCount === 1 ? '' : 's'} will fall outside working hours.`
          : 'No scheduled work is affected.',
      okText: 'Remove',
      danger: true,
      onOk: async () => {
        try {
          await calendarApi.remove(exc.id);
          message.success('Exception removed');
          setFormOpen(false);
          setEditing(null);
          await fetchMonth(anchor);
          void offerReschedule(exc.date, exc.date, `${TYPE_LABEL[exc.type]} removed on`);
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const { ref: shellRef, height: shellHeight } = useFitToScrollArea([]);
  const cells = monthCells(anchor);
  const weeks = cells.length / 7;
  const todayKey = shopNow().format('YYYY-MM-DD');
  const monthExceptions = [...exceptions]
    .filter((x) => dayjs(x.date).isSame(anchor, 'month'))
    .sort((a, b) => a.date.localeCompare(b.date));

  const monthStats = useMemo(() => {
    const daysInMonth = anchor.daysInMonth();
    let working = 0;
    for (let d = 1; d <= daysInMonth; d++) {
      const day = anchor.date(d);
      const key = day.format('YYYY-MM-DD');
      const exc = byDate.get(key);
      if (exc?.type === 'HOLIDAY_NO_WORK') continue;
      if (exc?.type === 'SPECIAL_WORKING_DAY') {
        working += 1;
        continue;
      }
      if (isNormalWorkingDay(day)) working += 1;
    }
    const holidays = monthExceptions.filter((x) => x.type === 'HOLIDAY_NO_WORK').length;
    const special = monthExceptions.filter((x) => x.type === 'SPECIAL_WORKING_DAY').length;
    const overtimeHours = monthExceptions
      .filter((x) => x.type === 'OVERTIME')
      .reduce((sum, x) => {
        if (!x.startTime || !x.endTime) return sum;
        const [sh, sm] = x.startTime.split(':').map(Number);
        const [eh, em] = x.endTime.split(':').map(Number);
        const mins = eh * 60 + em - (sh * 60 + sm);
        return sum + Math.max(0, mins) / 60;
      }, 0);
    return {
      working,
      holidays,
      special,
      overtimeHours: Math.round(overtimeHours * 10) / 10,
    };
  }, [anchor, byDate, monthExceptions]);

  function otHoursLabel(exc: WorkCalendarException): string {
    if (!exc.startTime || !exc.endTime) return 'OT';
    const [sh, sm] = exc.startTime.split(':').map(Number);
    const [eh, em] = exc.endTime.split(':').map(Number);
    const hrs = Math.max(0, eh * 60 + em - (sh * 60 + sm)) / 60;
    const rounded = Math.round(hrs * 10) / 10;
    return `OT +${rounded % 1 === 0 ? rounded.toFixed(0) : rounded}h`;
  }

  return (
    <div className="work-calendar-page ui-page">
      <div
        ref={shellRef}
        className="work-calendar__shell"
        style={shellHeight ? { height: shellHeight } : undefined}
      >
        <div className="work-calendar__toolbar">
          <div className="work-calendar__nav">
            <PeriodNavigator
              mode="month"
              value={anchor}
              onChange={(d) => setAnchor(d.startOf('month'))}
            />
            <Button size="small" onClick={() => setAnchor(shopToday())}>
              Today
            </Button>
          </div>
          <div className="work-calendar__legend">
            <Legend
              items={[
                { label: 'Overtime', tone: 'blue' },
                { label: 'Special day', tone: 'teal' },
                { label: 'Holiday', tone: 'red' },
                {
                  label: 'Day off',
                  pattern: 'repeating-linear-gradient(-45deg, #f8fafc, #f8fafc 3px, #e2e8f0 3px, #e2e8f0 6px)',
                  border: '1px solid #cbd5e1',
                },
              ]}
            />
            {shopBreak ? (
              <Button size="small" onClick={openBreak} disabled={!isAdmin}>
                Break {shopBreak.breakStart}–{shopBreak.breakEnd}
              </Button>
            ) : null}
            <span className="work-calendar__mode">
              {isAdmin ? 'Click a day to edit' : 'View only'}
            </span>
          </div>
        </div>

        <div className="work-calendar__body">
          {loading ? (
            <PageSkeleton variant="block" />
          ) : (
            <div
              className="work-calendar__grid"
              role="grid"
              aria-label="Work calendar"
              style={{ gridTemplateRows: `auto repeat(${weeks}, minmax(0, 1fr))` }}
            >
              {WEEKDAYS.map((d) => (
                <div key={d} className="work-calendar__weekday">
                  {d}
                </div>
              ))}
              {cells.map((day) => {
                const key = day.format('YYYY-MM-DD');
                const inMonth = day.month() === anchor.month();
                const exc = inMonth ? byDate.get(key) : undefined;
                const normal = isNormalWorkingDay(day);
                const classes = [
                  'work-calendar__cell',
                  inMonth ? '' : 'work-calendar__cell--outside',
                  inMonth && key === todayKey ? 'work-calendar__cell--today' : '',
                  inMonth && !normal && !exc ? 'work-calendar__cell--off' : '',
                  exc ? `work-calendar__cell--${exc.type.toLowerCase()}` : '',
                  inMonth && (isAdmin || exc) ? 'work-calendar__cell--interactive' : '',
                ]
                  .filter(Boolean)
                  .join(' ');

                return (
                  <button
                    key={key}
                    type="button"
                    className={classes}
                    disabled={!inMonth || (!isAdmin && !exc)}
                    onClick={() => {
                      if (isAdmin || exc) openDay(day);
                    }}
                    title={exc ? exceptionBadge(exc) : undefined}
                  >
                    <span className="work-calendar__day-num">{day.date()}</span>
                    {inMonth && key === todayKey ? (
                      <span className="work-calendar__today-pill">Today</span>
                    ) : null}
                    {exc?.type === 'OVERTIME' ? (
                      <span className="work-calendar__chip work-calendar__chip--ot">
                        {otHoursLabel(exc)}
                      </span>
                    ) : null}
                    {exc?.type === 'SPECIAL_WORKING_DAY' ? (
                      <span className="work-calendar__chip work-calendar__chip--special">
                        Special
                      </span>
                    ) : null}
                    {exc?.type === 'HOLIDAY_NO_WORK' ? (
                      <span className="work-calendar__chip work-calendar__chip--holiday">
                        {exc.note?.trim() || 'Holiday'}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          )}

          <aside className="work-calendar__aside" aria-label="This month">
            <div className="work-calendar__aside-title">This month</div>
            <div className="work-calendar__stats">
              <div className="work-calendar__stat">
                <div className="work-calendar__stat-n">{monthStats.working}</div>
                <div className="work-calendar__stat-l">Working days</div>
              </div>
              <div className="work-calendar__stat">
                <div className="work-calendar__stat-n">{monthStats.holidays}</div>
                <div className="work-calendar__stat-l">Holidays</div>
              </div>
              <div className="work-calendar__stat">
                <div className="work-calendar__stat-n">{monthStats.overtimeHours}</div>
                <div className="work-calendar__stat-l">Overtime hours</div>
              </div>
              <div className="work-calendar__stat">
                <div className="work-calendar__stat-n">{monthStats.special}</div>
                <div className="work-calendar__stat-l">Special days</div>
              </div>
            </div>
            {monthExceptions.length === 0 ? (
              <div className="work-calendar__aside-empty">
                Normal hours all month. No overtime, special days, or holidays marked.
              </div>
            ) : (
              <ul className="work-calendar__aside-list">
                {monthExceptions.map((x) => (
                  <li key={x.id}>
                    <button
                      type="button"
                      className={`work-calendar__aside-item work-calendar__aside-item--${x.type.toLowerCase()}`}
                      onClick={() => openDay(dayjs(x.date))}
                    >
                      <span className="work-calendar__aside-date">
                        {formatShop(x.date, 'ddd, MMM D')}
                      </span>
                      <span className="work-calendar__aside-what">
                        {x.type === 'HOLIDAY_NO_WORK'
                          ? x.note?.trim() || 'Holiday'
                          : x.type === 'OVERTIME'
                            ? otHoursLabel(x)
                            : `${excTitle(x)} · ${excDetail(x)}`}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>
        </div>
      </div>

      <Modal
        open={formOpen}
        onCancel={() => setFormOpen(false)}
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
            <CalendarOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">
              {!isAdmin
                ? 'Calendar exception'
                : editing
                  ? 'Edit calendar exception'
                  : 'Add calendar exception'}
            </div>
            {!isAdmin ? <div className="app-form-modal__sub">View only</div> : null}
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={() => setFormOpen(false)}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <Form form={form} layout="vertical" style={{ padding: '20px 24px 8px' }} disabled={!isAdmin}>
          {sectionLabel('Exception')}
          <Form.Item
            name="type"
            label="Type"
            rules={[{ required: true, message: 'Choose a type' }]}
            style={{ marginBottom: 18 }}
          >
            <Select
              onChange={onTypeChange}
              options={[
                { value: 'OVERTIME', label: 'Overtime (extend a working day)' },
                {
                  value: 'SPECIAL_WORKING_DAY',
                  label: 'Special working day (e.g. Sunday work)',
                },
                { value: 'HOLIDAY_NO_WORK', label: 'Holiday / closed' },
              ]}
            />
          </Form.Item>

          {sectionLabel('Schedule')}
          <Form.Item
            name="date"
            label={editing ? 'Date' : 'From date'}
            rules={[{ required: true, message: 'Pick a date' }]}
            style={{ marginBottom: 14 }}
          >
            <DatePicker style={{ width: '100%' }} format="MMM D, YYYY" disabledDate={isPastShopDay} />
          </Form.Item>
          {!editing && (
            <Form.Item
              name="dateTo"
              label="Through date (optional)"
              tooltip="Set an end date to create the same exception across a range, e.g. a week of OT."
              style={{ marginBottom: 14 }}
            >
              <DatePicker
                style={{ width: '100%' }}
                format="MMM D, YYYY"
                disabledDate={(d) =>
                  isPastShopDay(d) || Boolean(d && form.getFieldValue('date')?.isAfter?.(d, 'day'))
                }
              />
            </Form.Item>
          )}
          {watchType !== 'HOLIDAY_NO_WORK' && (
            <Row gutter={12}>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="startTime"
                  label="Start time"
                  rules={[{ required: true, message: 'Start time required' }]}
                  style={{ marginBottom: 14 }}
                >
                  <TimePicker format="HH:mm" minuteStep={15} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="endTime"
                  label="End time"
                  rules={[{ required: true, message: 'End time required' }]}
                  style={{ marginBottom: 14 }}
                >
                  <TimePicker format="HH:mm" minuteStep={15} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
          )}

          {sectionLabel('Notes')}
          <Form.Item name="note" label="Note (optional)" style={{ marginBottom: 14 }}>
            <Input.TextArea rows={2} placeholder="e.g. Rush order, company holiday…" />
          </Form.Item>
        </Form>

        <div className="app-form-modal__footer">
          {isAdmin && editing ? (
            <Button
              danger
              icon={<DeleteOutlined />}
              onClick={() => confirmDelete(editing)}
              style={{ marginRight: 'auto' }}
            >
              Remove
            </Button>
          ) : null}
          {isAdmin ? (
            <>
              <Button onClick={() => setFormOpen(false)} style={{ minWidth: 96 }}>
                Cancel
              </Button>
              <Button
                type="primary"
                loading={saving}
                onClick={submitForm}
                style={{ fontWeight: 700, minWidth: 120 }}
              >
                {editing ? 'Save' : 'Create'}
              </Button>
            </>
          ) : (
            <Button onClick={() => setFormOpen(false)} style={{ minWidth: 96 }}>
              Close
            </Button>
          )}
        </div>
      </Modal>

      <FormModal
        size="sm"
        icon={<ClockCircleOutlined />}
        open={breakOpen}
        title="Daily break"
        onCancel={() => setBreakOpen(false)}
        onOk={saveBreak}
        okText="Save"
        confirmLoading={breakSaving}
        forceRender
      >
        <p style={{ marginTop: 0, color: '#64748b' }}>Not counted as working time.</p>
        <Form form={breakForm} layout="vertical" requiredMark={false}>
          <Row gutter={12}>
            <Col xs={24} sm={12}>
              <Form.Item
                name="breakStart"
                label="Starts"
                rules={[{ required: true, message: 'Enter the start' }]}
              >
                <TimePicker format="HH:mm" minuteStep={15} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item
                name="breakEnd"
                label="Ends"
                dependencies={['breakStart']}
                rules={[
                  { required: true, message: 'Enter the end' },
                  ({ getFieldValue }) => ({
                    validator(_, value: Dayjs | undefined) {
                      const start = getFieldValue('breakStart') as Dayjs | undefined;
                      if (!value || !start || value.isAfter(start)) return Promise.resolve();
                      return Promise.reject(new Error('Must end after it starts'));
                    },
                  }),
                ]}
              >
                <TimePicker format="HH:mm" minuteStep={15} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </FormModal>

      {affected ? (
        <RescheduleAffectedJobs
          changeLabel={affected.changeLabel}
          jobs={affected.jobs}
          onClose={() => setAffected(null)}
        />
      ) : null}
    </div>
  );
}
