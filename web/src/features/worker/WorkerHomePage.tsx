import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button } from 'antd';
import {
  CalendarOutlined,
  CaretRightFilled,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  RightOutlined,
  StarFilled,
  ToolOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { analyticsApi } from '../../api/analytics.api';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { toolsApi } from '../../api/tools.api';
import { useAuth } from '../../hooks/useAuth';
import { WorkerPageHeader } from '../../layouts/WorkerLayout';
import { useNavMemory } from '../../navigation/navMemory';
import { useOffline } from '../../offline/OfflineProvider';
import { offlineCache } from '../../offline/offlineCache';
import { overlayOperations, type OverlaidOperation } from '../../offline/overlay';
import SyncChip from '../../offline/SyncChip';
import { LevelBar, PageSkeleton, StatusPill } from '../../components/ui';
import { SHOP_TZ, formatShop } from '../../utils/shopTime';
import type { MyWorkSummary, Operation, ToolUnit } from '../../types';
import {
  announceRecorded,
  canStartOperation,
  isMyOperation,
  operationActionLabel,
  showActionError,
} from '../my-assignments/operationActions';
import { machineLabel, opStatusPill, opTitle } from '../my-assignments/opStatus';
import { useMyOperations } from '../my-assignments/useMyOperations';
import { TOOLS_CHANGED } from './toolsEvents';
import { useMyProfile } from './useMyProfile';
import { formatRange, greeting, summarizeSchedule, weekInWords } from './workerFormat';
import { formatHoursMinutes } from '../../utils/duration';

const STARTABLE = new Set(['PENDING', 'SCHEDULED', 'REWORK']);

function firstSegmentStart(op: Operation): number {
  const t = op.segments?.[0]?.start || op.scheduledStart;
  return t ? dayjs(t).valueOf() : Number.MAX_SAFE_INTEGER;
}

/** The operation to show big: running, then paused, then the next one to start. */
function pickHero(ops: OverlaidOperation[], userId: string | null) {
  const mine = ops.filter((o) => isMyOperation(o, userId));
  const running = mine.find((o) => o.status === 'IN_PROGRESS' && !o.isPaused);
  if (running) return running;
  const paused = mine.find((o) => o.status === 'IN_PROGRESS');
  if (paused) return paused;
  return [...mine]
    .filter((o) => STARTABLE.has(o.status))
    .sort(
      (a, b) =>
        firstSegmentStart(a) - firstSegmentStart(b) ||
        (a.dueDate || '9999').localeCompare(b.dueDate || '9999') ||
        a.sequenceNo - b.sequenceNo
    )[0];
}

/** Today's part of an operation's planned time, in shop time. */
function todaySpan(op: Operation, todayKey: string): { start: dayjs.Dayjs; end: dayjs.Dayjs } | null {
  const segs = op.segments?.length
    ? op.segments
    : op.scheduledStart && op.scheduledEnd
      ? [{ start: op.scheduledStart, end: op.scheduledEnd }]
      : [];
  let start: dayjs.Dayjs | null = null;
  let end: dayjs.Dayjs | null = null;
  for (const seg of segs) {
    const s = dayjs(seg.start).tz(SHOP_TZ);
    const e = dayjs(seg.end).tz(SHOP_TZ);
    if (s.format('YYYY-MM-DD') !== todayKey && e.format('YYYY-MM-DD') !== todayKey) continue;
    if (!start || s.isBefore(start)) start = s;
    if (!end || e.isAfter(end)) end = e;
  }
  return start && end ? { start, end } : null;
}

function runningSince(op: Operation): string | null {
  const logs = [...(op.timeLogs || [])].sort((a, b) => dayjs(a.eventAt).valueOf() - dayjs(b.eventAt).valueOf());
  const last = logs[logs.length - 1];
  return last && (last.event === 'START' || last.event === 'RESUME') ? last.eventAt : null;
}

function fmtHours(h: number) {
  return `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
}

type Stat = {
  key: string;
  icon: ReactNode;
  label: string;
  value: string;
  sub: ReactNode;
  onClick?: () => void;
};

function StatTiles({ stats }: { stats: Stat[] }) {
  return (
    <div className="w-stats">
      {stats.map((s) => {
        const body = (
          <>
            <span className="w-stat__label">
              {s.icon}
              {s.label}
            </span>
            <span className="w-stat__value">{s.value}</span>
            <span className="w-stat__sub">{s.sub}</span>
          </>
        );
        return s.onClick ? (
          <button key={s.key} type="button" className="w-stat w-stat--link" onClick={s.onClick}>
            {body}
          </button>
        ) : (
          <div key={s.key} className="w-stat">
            {body}
          </div>
        );
      })}
    </div>
  );
}

function elapsed(fromIso: string, nowMs: number) {
  const mins = Math.max(0, Math.floor((nowMs - dayjs(fromIso).valueOf()) / 60000));
  const h = Math.floor(mins / 60);
  return h ? `${h}h ${mins % 60}m` : `${mins}m`;
}

export default function WorkerHomePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { online, userId, pending, version, record } = useOffline();
  const { operations, loading, error } = useMyOperations();
  const { resolveSectionNav } = useNavMemory();
  const { me, skills } = useMyProfile(online);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [acting, setActing] = useState(false);
  const [tools, setTools] = useState<ToolUnit[] | null>(null);
  const [summary, setSummary] = useState<MyWorkSummary | null>(null);
  const [heroJobOps, setHeroJobOps] = useState<Operation[] | null>(null);

  const goSection = (key: string) => navigate(resolveSectionNav(key).to);
  const openJob = (op: Operation) => navigate(`/my-assignments/${op.jobOrderId}`);

  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (!online) return;
    let cancelled = false;
    const load = () => {
      toolsApi
        .myTools()
        .then(({ data }) => !cancelled && setTools(data))
        .catch(() => undefined);
    };
    load();
    window.addEventListener(TOOLS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(TOOLS_CHANGED, load);
    };
  }, [online]);

  useEffect(() => {
    if (!online) return;
    analyticsApi
      .mySummary()
      .then(({ data }) => setSummary(data))
      .catch(() => undefined);
  }, [online, version]);

  const hero = useMemo(() => pickHero(operations, userId), [operations, userId]);
  const heroId = hero?.id;
  const heroJobId = hero?.jobOrderId;

  // The job's other operations decide whether Start is allowed, as on the job page.
  useEffect(() => {
    if (!heroJobId || !userId) {
      setHeroJobOps(null);
      return;
    }
    let cancelled = false;
    const saved = offlineCache.read(userId).jobs[heroJobId];
    setHeroJobOps(saved?.operations ?? null);
    if (online) {
      jobOrdersApi
        .get(heroJobId)
        .then(({ data }) => {
          if (cancelled) return;
          offlineCache.saveJob(userId, data);
          setHeroJobOps(data.operations || []);
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [heroJobId, userId, online, version]);

  const jobOps = useMemo(
    () => (heroJobOps ? overlayOperations(heroJobOps, pending) : null),
    [heroJobOps, pending]
  );

  const todayKey = dayjs().tz(SHOP_TZ).format('YYYY-MM-DD');
  const queue = useMemo(
    () =>
      operations
        .filter((o) => o.id !== heroId && o.status !== 'COMPLETED' && isMyOperation(o, userId))
        .map((op) => ({ op, span: todaySpan(op, todayKey) }))
        .filter((r): r is { op: OverlaidOperation; span: NonNullable<ReturnType<typeof todaySpan>> } =>
          Boolean(r.span)
        )
        .sort((a, b) => a.span.start.valueOf() - b.span.start.valueOf()),
    [operations, heroId, userId, todayKey]
  );

  // A ref, not only state: a second tap can arrive before the re-render.
  const actingRef = useRef(false);
  const act = async (op: Operation, kind: 'start' | 'resume') => {
    if (acting || actingRef.current) return;
    actingRef.current = true;
    setActing(true);
    try {
      const outcome = await record({
        kind,
        label: operationActionLabel(op, kind === 'start' ? 'Start' : 'Resume', op.jobNumber),
        operationId: op.id,
        jobOrderId: op.jobOrderId,
      });
      announceRecorded(outcome, online, kind === 'start' ? 'Operation started' : 'Operation resumed');
    } catch (err) {
      showActionError(err);
    } finally {
      setActing(false);
      window.setTimeout(() => {
        actingRef.current = false;
      }, 1500);
    }
  };

  const firstName = user?.nickname || user?.fullName?.split(' ')[0] || '';
  const todayDow = (dayjs().tz(SHOP_TZ).day() + 6) % 7;
  const todayHours = me?.schedules?.find((s) => s.dayOfWeek === todayDow);
  const todayLine = dayjs().tz(SHOP_TZ).format('ddd, MMM D');

  const renderHero = () => {
    if (!hero) {
      return (
        <div className="w-card">
          <div className="w-hero__state">Up next</div>
          <div className="w-hero__title">No operations waiting for you</div>
          <div className="w-hero__job">New jobs show up here and in Jobs as soon as they are assigned.</div>
        </div>
      );
    }
    const running = hero.status === 'IN_PROGRESS' && !hero.isPaused;
    const paused = hero.status === 'IN_PROGRESS' && hero.isPaused;
    const since = running ? runningSince(hero) : null;
    const startable = !running && !paused && jobOps ? canStartOperation(hero, jobOps, userId) : false;
    const blockedBy =
      !running && !paused && jobOps && !startable
        ? jobOps.find((o) => o.id !== hero.id && o.status !== 'COMPLETED' && o.sequenceNo < hero.sequenceNo)
        : undefined;
    const pill = opStatusPill(hero);
    const pillAddsInfo = !['Paused', 'In progress', 'Scheduled', 'Pending'].includes(pill.label);
    const next = hero.segments?.[0]?.start || hero.scheduledStart;

    return (
      <div className={`w-card w-hero${running ? ' w-hero--running' : ''}`}>
        <div className="w-hero__state">
          <span>{running ? 'Working on' : paused ? 'Paused' : 'Up next'}</span>
          {running ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, textTransform: 'none' }}>
              {hero.syncState && <SyncChip state={hero.syncState} />}
              <span className="w-hero__timer">{since ? elapsed(since, nowMs) : 'Running'}</span>
            </span>
          ) : (
            <span style={{ display: 'inline-flex', gap: 4, letterSpacing: 0, textTransform: 'none' }}>
              {hero.syncState && <SyncChip state={hero.syncState} />}
              {pillAddsInfo && (
                <StatusPill tone={pill.tone} compact>
                  {pill.label}
                </StatusPill>
              )}
            </span>
          )}
        </div>
        <div className="w-hero__title">{opTitle(hero)}</div>
        <div className="w-hero__job">
          {[hero.jobNumber, hero.clientName].filter(Boolean).join(' · ')}
        </div>
        <div className="w-facts">
          <div>
            <div className="w-fact__label">Machine</div>
            <div className="w-fact__value">{machineLabel(hero) || '—'}</div>
          </div>
          <div>
            <div className="w-fact__label">Target</div>
            <div className="w-fact__value">
              {formatHoursMinutes(hero.estimatedHours)}
            </div>
          </div>
          <div>
            <div className="w-fact__label">{running || paused ? 'Due' : 'Planned'}</div>
            <div className="w-fact__value">
              {running || paused
                ? hero.dueDate
                  ? formatShop(hero.dueDate, 'MMM D')
                  : '—'
                : next
                  ? formatShop(next, 'ddd h:mm A')
                  : '—'}
            </div>
          </div>
        </div>
        {paused ? (
          <Button
            type="primary"
            block
            className="w-action"
            icon={<CaretRightFilled />}
            loading={acting}
            onClick={() => act(hero, 'resume')}
          >
            Resume
          </Button>
        ) : startable ? (
          <Button
            type="primary"
            block
            className="w-action"
            icon={<CaretRightFilled />}
            loading={acting}
            onClick={() => act(hero, 'start')}
          >
            Start
          </Button>
        ) : (
          <Button block className="w-action" type={running ? 'primary' : 'default'} onClick={() => openJob(hero)}>
            Open job
          </Button>
        )}
        {running && <div className="w-hero__hint">Pause or mark complete on the job page.</div>}
        {blockedBy && (
          <div className="w-hero__hint">Starts after Op {blockedBy.sequenceNo} is finished.</div>
        )}
        {(paused || startable) && (
          <button type="button" className="w-link w-hero__details" onClick={() => openJob(hero)}>
            Job details <RightOutlined style={{ fontSize: 10 }} />
          </button>
        )}
      </div>
    );
  };

  const heroToday =
    hero && (hero.status === 'IN_PROGRESS' || todaySpan(hero, todayKey)) ? 1 : 0;
  const leftToday = queue.length + heroToday;
  const nextLater = queue.find((r) => r.span.start.valueOf() > nowMs);
  const week = online ? summary?.thisWeek : undefined;
  const weekHours = week ? (week.loggedHours ?? week.hoursWorked) : null;
  const doneDiff = week && summary?.lastWeek ? week.finishedOperations - summary.lastWeek.finishedOperations : null;
  const unknown = online ? 'Loading…' : 'Offline';
  const stats: Stat[] = [
    {
      key: 'today',
      icon: <CalendarOutlined />,
      label: 'Left today',
      value: String(leftToday),
      sub: nextLater ? `Next ${nextLater.span.start.format('h:mm A')}` : leftToday ? 'On your list' : 'All clear',
      onClick: () => goSection('/schedule'),
    },
    {
      key: 'done',
      icon: <CheckCircleOutlined />,
      label: 'Done this week',
      value: week ? String(week.finishedOperations) : '—',
      sub: !week ? (
        unknown
      ) : doneDiff == null ? (
        'Since Monday'
      ) : doneDiff === 0 ? (
        'Same as last week'
      ) : (
        <span className={doneDiff > 0 ? 'w-trend__up' : 'w-trend__down'}>
          {doneDiff > 0 ? '↑' : '↓'} {Math.abs(doneDiff)} vs last week
        </span>
      ),
    },
    {
      key: 'hours',
      icon: <ClockCircleOutlined />,
      label: 'Hours this week',
      value: weekHours != null ? fmtHours(weekHours) : '—',
      sub: week ? 'On your operations' : unknown,
    },
    {
      key: 'tools',
      icon: <ToolOutlined />,
      label: 'Tools out',
      value: online && tools ? String(tools.length) : '—',
      sub: !online || !tools ? unknown : tools.length ? 'Out with you' : 'None out',
      onClick: () => goSection('/inventory'),
    },
  ];

  const words = summary ? weekInWords(summary.thisWeek, summary.lastWeek) : null;
  const hoursLines = summarizeSchedule(me?.schedules);
  const machines = me?.assignedMachines || [];

  return (
    <div>
      <WorkerPageHeader
        title={firstName ? `${greeting(dayjs().tz(SHOP_TZ).hour())}, ${firstName}` : 'Dashboard'}
        subtitle={
          todayHours?.isWorking && todayHours.startTime && todayHours.endTime
            ? `${todayLine} · ${formatRange(todayHours.startTime, todayHours.endTime)}`
            : todayHours && !todayHours.isWorking
              ? `${todayLine} · Day off`
              : todayLine
        }
      />

      {loading ? (
        <PageSkeleton variant="cards" />
      ) : (
        <div className="w-page">
          {error ? (
            <div className="w-card" style={{ color: 'var(--tone-red-fg)', fontSize: 14 }}>
              {error}
            </div>
          ) : (
            renderHero()
          )}

          <StatTiles stats={stats} />

          <section className="w-card w-card--flush">
            <div className="w-card__head">
              <span className="w-card__title">
                {hero ? 'Later today' : 'Today'}
                {queue.length > 0 && <span className="w-card__count"> · {queue.length}</span>}
              </span>
              <button type="button" className="w-link" onClick={() => goSection('/schedule')}>
                Schedule <RightOutlined style={{ fontSize: 10 }} />
              </button>
            </div>
            {queue.length === 0 ? (
              <div className="w-dayoff">Nothing else planned for today.</div>
            ) : (
              <div className="w-rows">
                {queue.map(({ op, span }) => {
                  const pill = opStatusPill(op);
                  return (
                    <button key={op.id} type="button" className="w-row" onClick={() => openJob(op)}>
                      <div className="w-row__time">
                        {span.start.format('h:mm A')}
                        <span>{span.end.format('h:mm A')}</span>
                      </div>
                      <div className="w-row__main">
                        <div className="w-row__title">{opTitle(op)}</div>
                        <div className="w-row__sub">
                          {[op.jobNumber, machineLabel(op)].filter(Boolean).join(' · ')}
                        </div>
                      </div>
                      <StatusPill tone={pill.tone} compact>
                        {pill.label}
                      </StatusPill>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="w-card w-card--flush">
            <div className="w-card__head">
              <span className="w-card__title">
                Tools you&apos;re holding
                {tools ? <span className="w-card__count"> · {tools.length}</span> : null}
              </span>
              <button type="button" className="w-link" onClick={() => goSection('/inventory')}>
                Inventory <RightOutlined style={{ fontSize: 10 }} />
              </button>
            </div>
            {!online ? (
              <div className="w-dayoff">Connect to see the tools out with you.</div>
            ) : !tools ? (
              <div className="w-dayoff">Loading…</div>
            ) : tools.length === 0 ? (
              <div className="w-dayoff">No tools out with you. Scan a tool&apos;s QR to borrow it.</div>
            ) : (
              <div className="w-rows">
                {tools.slice(0, 3).map((unit) => (
                  <button key={unit.id} type="button" className="w-row" onClick={() => goSection('/inventory')}>
                    <span className="w-row__icon">
                      <ToolOutlined />
                    </span>
                    <div className="w-row__main">
                      <div className="w-row__title">{unit.toolTypeName}</div>
                      <div className="w-row__sub">
                        {unit.assetCode}
                        {unit.heldSince ? ` · since ${formatShop(unit.heldSince, 'MMM D, h:mm A')}` : ''}
                      </div>
                    </div>
                    <RightOutlined className="w-row__chev" />
                  </button>
                ))}
                {tools.length > 3 && (
                  <button type="button" className="w-row" style={{ minHeight: 44 }} onClick={() => goSection('/inventory')}>
                    <span className="w-link" style={{ margin: 0 }}>
                      See all {tools.length}
                    </span>
                  </button>
                )}
              </div>
            )}
          </section>

          {words && (
            <section className="w-card">
              <div className="w-card__head">
                <span className="w-card__title">Your week</span>
              </div>
              <div className="w-trend">
                <div className="w-trend__line">
                  <CheckCircleOutlined />
                  <span>
                    {words.finished}
                    {words.change ? (
                      <>
                        {', '}
                        <span
                          className={
                            words.change.up === true
                              ? 'w-trend__up'
                              : words.change.up === false
                                ? 'w-trend__down'
                                : undefined
                          }
                        >
                          {words.change.text}
                        </span>
                      </>
                    ) : null}
                    .
                  </span>
                </div>
                {words.hours && (
                  <div className="w-trend__line">
                    <ClockCircleOutlined />
                    <span>{words.hours}.</span>
                  </div>
                )}
                {summary && summary.thisWeek.redoOperations > 0 && (
                  <div className="w-trend__line">
                    <ExclamationCircleOutlined />
                    <span>
                      {summary.thisWeek.redoOperations} of them{' '}
                      {summary.thisWeek.redoOperations === 1 ? 'was a redo' : 'were redos'}.
                    </span>
                  </div>
                )}
              </div>
            </section>
          )}

          {me && (
            <section className="w-card">
              <div className="w-card__head">
                <span className="w-card__title">My profile</span>
                <button type="button" className="w-link" onClick={() => navigate('/account/profile')}>
                  View <RightOutlined style={{ fontSize: 10 }} />
                </button>
              </div>
              <div className="w-profile-grid">
                <div className="w-profile-line">
                  <span className="w-profile-line__label">Skills</span>
                  <span className="w-profile-line__value">
                    {skills.length === 0
                      ? 'None set yet'
                      : skills.slice(0, 3).map((s) => (
                          <div key={s.machineTypeId} className="w-skill">
                            <span>
                              {s.machineTypeName || s.machineTypeCode}
                              {s.isPrimary && <StarFilled className="w-skill__primary" aria-label="Primary" />}
                            </span>
                            <LevelBar level={s.proficiency} />
                          </div>
                        ))}
                    {skills.length > 3 && <div className="w-muted">+{skills.length - 3} more</div>}
                  </span>
                </div>
                <div className="w-profile-line">
                  <span className="w-profile-line__label">Machine</span>
                  <span className="w-profile-line__value">
                    {machines.length ? machines.map((m) => m.label).join(', ') : 'Not assigned'}
                  </span>
                </div>
                <div className="w-profile-line">
                  <span className="w-profile-line__label">Hours</span>
                  <span className="w-profile-line__value">
                    {hoursLines.length ? hoursLines.slice(0, 2).map((l) => <div key={l}>{l}</div>) : 'Shop hours'}
                    {hoursLines.length > 2 && <div className="w-muted">+{hoursLines.length - 2} more</div>}
                  </span>
                </div>
              </div>
            </section>
          )}

          {!online && (
            <div className="w-note" style={{ textAlign: 'center', padding: '0 12px' }}>
              Offline. Start and Resume still work.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
