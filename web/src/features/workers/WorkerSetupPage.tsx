import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Select, Spin, Switch, Tooltip, Typography, message } from 'antd';
import { SearchOutlined, StarFilled, StarOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { MOBILE_SHELL_QUERY, NARROW_QUERY, useMediaQuery, useViewport } from '../../hooks/useViewport';

const SKILL_CARDS_QUERY = '(max-width: 1439.98px)';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { usersApi, workerProfileApi } from '../../api/users.api';
import { getErrorMessage } from '../../api/client';
import type { SkillEvidence, User, WorkerSchedule, WorkerSkill } from '../../types';
import UserIdentity from '../../components/UserIdentity';
import WorkerHistoryPanel from './WorkerHistoryPanel';
import { LevelGuide, SkillRecord, SkillSuggestion, levelName } from './SkillEvidence';
import { formatShop } from '../../utils/shopTime';
import './workerSetup.css';
import InfoTip from '../../components/InfoTip';
import { LevelBar, TabBar, PageSkeleton } from '../../components/ui';
import ResponsiveTable from '../../components/ui/ResponsiveTable';

const { Text } = Typography;

const DAY_LABELS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

type DetailTab = 'skills' | 'hours' | 'history';

type SkillRow = {
  /** Machine type id */
  key: string;
  name: string;
  enabled: boolean;
  proficiency: number;
  isPrimary: boolean;
};

function defaultSchedule(): WorkerSchedule[] {
  return DAY_LABELS.map((_, dow) => ({
    dayOfWeek: dow,
    isWorking: dow < 6,
    startTime: dow < 6 ? '08:00' : null,
    endTime: dow < 6 ? '17:00' : null,
  }));
}

export default function WorkerSetupPage() {
  const [params, setParams] = useSearchParams();
  const workerId = params.get('worker') || '';
  const detailTab: DetailTab =
    params.get('panel') === 'hours'
      ? 'hours'
      : params.get('panel') === 'history'
        ? 'history'
        : 'skills';

  const [workers, setWorkers] = useState<User[]>([]);
  const [query, setQuery] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [skillRows, setSkillRows] = useState<SkillRow[]>([]);
  const [schedule, setSchedule] = useState<WorkerSchedule[]>(defaultSchedule());
  const [savingSkills, setSavingSkills] = useState(false);
  const [evidence, setEvidence] = useState<SkillEvidence | null>(null);
  const [savingSchedule, setSavingSchedule] = useState(false);

  const { isNarrow } = useViewport();
  const compact = useMediaQuery(MOBILE_SHELL_QUERY);
  // The skills table needs about 900px for its six columns; the detail pane only
  // gets that on wide screens, so tablets and small laptops use the skill cards.
  const skillCards = useMediaQuery(SKILL_CARDS_QUERY);
  const pushedDetail = useRef(false);

  const setWorker = (id: string) => {
    const nextParams = new URLSearchParams(params);
    nextParams.delete('tab');
    nextParams.set('worker', id);
    if (!nextParams.get('panel')) nextParams.set('panel', 'skills');
    // On narrow screens the list and the detail are separate views, so Back should return to the list.
    pushedDetail.current = isNarrow && !workerId;
    setParams(nextParams, { replace: !pushedDetail.current });
  };

  const setDetailTab = (next: DetailTab) => {
    const nextParams = new URLSearchParams(params);
    nextParams.set('panel', next);
    setParams(nextParams, { replace: true });
  };

  useEffect(() => {
    (async () => {
      setListLoading(true);
      try {
        const { data } = await usersApi.list();
        // Only production workers are assigned work.
        const production = data
          .filter((u) => u.role === 'PRODUCTION_WORKER' && u.status !== 'DISABLED')
          .sort((a, b) => a.fullName.localeCompare(b.fullName));
        setWorkers(production);
        if (!workerId && production[0] && !window.matchMedia(NARROW_QUERY).matches) {
          const nextParams = new URLSearchParams(params);
          nextParams.delete('tab');
          nextParams.set('worker', production[0].id);
          nextParams.set('panel', 'skills');
          setParams(nextParams, { replace: true });
        }
      } catch (err) {
        message.error(getErrorMessage(err));
      } finally {
        setListLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!workerId) return;
    const load = async () => {
      setDetailLoading(true);
      try {
        setEvidence(null);
        const [machinesRes, skillsRes, scheduleRes, evidenceRes] = await Promise.all([
          jobOrdersApi.machines(),
          workerProfileApi.getSkills(workerId).catch(() => ({ data: [] as WorkerSkill[] })),
          workerProfileApi.getSchedule(workerId).catch(() => ({ data: [] as WorkerSchedule[] })),
          workerProfileApi.getSkillEvidence(workerId).catch(() => ({ data: null })),
        ]);
        setEvidence(evidenceRes.data);
        const existing = new Map((skillsRes.data || []).map((s) => [s.machineTypeId, s]));
        setSkillRows(
          machinesRes.data.map((m) => {
            const skill = existing.get(m.id || '');
            return {
              key: m.id || '',
              name: m.name,
              enabled: Boolean(skill),
              proficiency: skill?.proficiency ?? 3,
              isPrimary: skill?.isPrimary ?? false,
            };
          })
        );
        if (scheduleRes.data?.length === 7) {
          setSchedule([...scheduleRes.data].sort((a, b) => a.dayOfWeek - b.dayOfWeek));
        } else {
          setSchedule(defaultSchedule());
        }
      } catch (err) {
        message.error(getErrorMessage(err));
      } finally {
        setDetailLoading(false);
      }
    };
    load();
  }, [workerId]);

  const filteredWorkers = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return workers;
    return workers.filter((w) =>
      `${w.fullName} ${w.nickname || ''} ${w.email || ''} ${w.mobileNumber || ''}`.toLowerCase().includes(q)
    );
  }, [workers, query]);

  const selected = workers.find((w) => w.id === workerId) || null;

  const saveSkills = async () => {
    if (!workerId) return;
    const enabled = skillRows.filter((r) => r.enabled);
    if (enabled.length && !enabled.some((r) => r.isPrimary)) {
      message.warning('Mark one enabled skill as primary');
      return;
    }
    setSavingSkills(true);
    try {
      await workerProfileApi.putSkills(
        workerId,
        enabled.map((r) => ({
          machineTypeId: r.key,
          proficiency: r.proficiency,
          isPrimary: r.isPrimary,
        }))
      );
      message.success('Skills saved');
      workerProfileApi
        .getSkillEvidence(workerId)
        .then((res) => setEvidence(res.data))
        .catch(() => undefined);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSavingSkills(false);
    }
  };

  const saveSchedule = async () => {
    if (!workerId) return;
    setSavingSchedule(true);
    try {
      await workerProfileApi.putSchedule(
        workerId,
        schedule.map((d) => ({
          dayOfWeek: d.dayOfWeek,
          isWorking: d.isWorking,
          startTime: d.isWorking ? d.startTime : null,
          endTime: d.isWorking ? d.endTime : null,
        }))
      );
      message.success('Hours saved');
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSavingSchedule(false);
    }
  };

  const evidenceByMachine = useMemo(
    () => new Map((evidence?.machineTypes || []).map((e) => [e.machineTypeId, e])),
    [evidence]
  );
  const setLevel = (key: string, level: number) =>
    setSkillRows((prev) => prev.map((r) => (r.key === key ? { ...r, proficiency: level } : r)));

  const skillColumns = [
    {
      title: 'Can do',
      dataIndex: 'enabled',
      width: 84,
      render: (_: unknown, row: SkillRow) => (
        <Switch
          checked={row.enabled}
          onChange={(checked) => {
            setSkillRows((prev) =>
              prev.map((r) =>
                r.key === row.key
                  ? {
                      ...r,
                      enabled: checked,
                      isPrimary: checked ? r.isPrimary : false,
                    }
                  : r
              )
            );
          }}
        />
      ),
    },
    {
      title: 'Machine',
      key: 'target',
      width: 120,
      render: (_: unknown, row: SkillRow) => <strong>{row.name}</strong>,
    },
    {
      title: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          Level
          <InfoTip label="What each level means" title="Skill levels" content={<LevelGuide />} />
        </span>
      ),
      dataIndex: 'proficiency',
      width: 176,
      render: (_: unknown, row: SkillRow) =>
        row.enabled ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            <InputNumber
              min={1}
              max={5}
              value={row.proficiency}
              aria-label={`${row.name} level`}
              style={{ width: 64 }}
              onChange={(v) => {
                setSkillRows((prev) =>
                  prev.map((r) =>
                    r.key === row.key ? { ...r, proficiency: Number(v) || 1 } : r
                  )
                );
              }}
            />
            <Tooltip title={levelName(row.proficiency)}>
              <span>
                <LevelBar level={row.proficiency} />
              </span>
            </Tooltip>
          </span>
        ) : (
          <span className="ui-muted-note">Can&apos;t run</span>
        ),
    },
    {
      title: 'Record (12 months)',
      key: 'record',
      render: (_: unknown, row: SkillRow) => <SkillRecord ev={evidenceByMachine.get(row.key)} />,
    },
    {
      title: 'Suggested',
      key: 'suggested',
      width: 136,
      render: (_: unknown, row: SkillRow) => (
        <SkillSuggestion
          ev={evidenceByMachine.get(row.key)}
          current={row.proficiency}
          enabled={row.enabled}
          onUse={(level) => setLevel(row.key, level)}
        />
      ),
    },
    {
      title: 'Primary',
      dataIndex: 'isPrimary',
      width: 76,
      render: (_: unknown, row: SkillRow) => (
        <Checkbox
          checked={row.isPrimary}
          disabled={!row.enabled}
          onChange={(e) => {
            const on = e.target.checked;
            setSkillRows((prev) =>
              prev.map((r) => ({
                ...r,
                isPrimary: r.key === row.key ? on : on ? false : r.isPrimary,
              }))
            );
          }}
        />
      ),
    },
  ];

  const scheduleColumns = [
    {
      title: 'Day',
      dataIndex: 'dayOfWeek',
      render: (dow: number) => DAY_LABELS[dow],
    },
    {
      title: 'Working',
      dataIndex: 'isWorking',
      width: 90,
      render: (_: unknown, row: WorkerSchedule) => (
        <Switch
          checked={row.isWorking}
          onChange={(checked) => {
            setSchedule((prev) =>
              prev.map((d) =>
                d.dayOfWeek === row.dayOfWeek
                  ? {
                      ...d,
                      isWorking: checked,
                      startTime: checked ? d.startTime || '08:00' : null,
                      endTime: checked ? d.endTime || '17:00' : null,
                    }
                  : d
              )
            );
          }}
        />
      ),
    },
    {
      title: 'Start',
      dataIndex: 'startTime',
      render: (_: unknown, row: WorkerSchedule) => (
        <Select
          disabled={!row.isWorking}
          value={row.startTime || undefined}
          style={{ width: 110 }}
          options={Array.from({ length: 24 }, (_, h) => {
            const v = `${String(h).padStart(2, '0')}:00`;
            return { value: v, label: v };
          })}
          onChange={(v) => {
            setSchedule((prev) =>
              prev.map((d) => (d.dayOfWeek === row.dayOfWeek ? { ...d, startTime: v } : d))
            );
          }}
        />
      ),
    },
    {
      title: 'End',
      dataIndex: 'endTime',
      render: (_: unknown, row: WorkerSchedule) => (
        <Select
          disabled={!row.isWorking}
          value={row.endTime || undefined}
          style={{ width: 110 }}
          options={Array.from({ length: 24 }, (_, h) => {
            const v = `${String(h).padStart(2, '0')}:00`;
            return { value: v, label: v };
          })}
          onChange={(v) => {
            setSchedule((prev) =>
              prev.map((d) => (d.dayOfWeek === row.dayOfWeek ? { ...d, endTime: v } : d))
            );
          }}
        />
      ),
    },
  ];

  return (
    <div className="worker-setup-page">
      <div
        className={`worker-setup-grid${isNarrow ? (workerId ? ' worker-setup-grid--detail' : ' worker-setup-grid--list') : ''}`}
      >
        <aside className="worker-setup-list">
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10 }}>
            <Input
              allowClear
              placeholder="Search workers…"
              prefix={<SearchOutlined aria-hidden />}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <InfoTip
              label="How this works"
              content={
                <>
                  Skills and weekly hours of production workers. Accounts are added under Users
                  &amp; Roles.
                </>
              }
            />
          </div>
          {listLoading ? (
            <div style={{ padding: 24, textAlign: 'center' }}>
              <Spin />
            </div>
          ) : filteredWorkers.length === 0 ? (
            <Text type="secondary">No workers yet. Create them under Users & Roles.</Text>
          ) : (
            <div className="worker-setup-list__items">
              {filteredWorkers.map((w) => {
                const active = w.id === workerId;
                return (
                  <button
                    key={w.id}
                    type="button"
                    className={`worker-setup-list__item${active ? ' is-active' : ''}`}
                    onClick={() => setWorker(w.id)}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, textAlign: 'left' }}
                  >
                    <UserIdentity
                      variant="list"
                      userId={w.id}
                      fullName={w.fullName}
                      nickname={w.nickname}
                      email={w.email}
                      mobileNumber={w.mobileNumber}
                    />
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        <div className="worker-setup-detail">
          {!workerId ? (
            <Text type="secondary">Select a worker to edit skills, hours, or history.</Text>
          ) : detailLoading ? (
            <PageSkeleton variant="block" />
          ) : (
            <>
              <div className="ws-hero">
                {selected ? (
                  <UserIdentity
                    userId={selected.id}
                    fullName={selected.fullName}
                    nickname={selected.nickname}
                    email={selected.email}
                    mobileNumber={selected.mobileNumber}
                    size={48}
                    lockSize
                  />
                ) : (
                  'Worker'
                )}
              </div>

              <div className="ws-tabs">
                <TabBar<DetailTab>
                  value={detailTab}
                  onChange={setDetailTab}
                  items={[
                    { label: 'Skills', value: 'skills' },
                    { label: 'Weekly hours', value: 'hours' },
                    { label: 'History', value: 'history' },
                  ]}
                />
              </div>


              {detailTab === 'skills' ? (
                <div className="worker-setup-panel">
                  <div className="worker-setup-panel__head">
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      Skills
                      <InfoTip
                        label="How skills work"
                        content="Switch on the machines they can operate, each at level 1 to 5. One primary skill only. Operations without a machine take no skill: anyone can do them. A machine nobody has a skill for yet is open to everyone. The record and the suggested level come from the last 12 months of finished operations; you decide the level."
                      />
                      {evidence?.lastReviewed ? (
                        <span className="ws-reviewed">
                          Last saved {formatShop(evidence.lastReviewed.at, 'MMM D, YYYY')}
                          {evidence.lastReviewed.byName ? ` by ${evidence.lastReviewed.byName}` : ''}
                        </span>
                      ) : null}
                    </span>
                    <div className="worker-setup-panel__save m-action-bar">
                      <Button
                        type="primary"
                        loading={savingSkills}
                        onClick={saveSkills}
                        style={{ fontWeight: 600 }}
                      >
                        Save skills
                      </Button>
                    </div>
                  </div>
                  {skillCards ? (
                    <div className="wsm-list">
                      {skillRows.map((row) => {
                        const ev = evidenceByMachine.get(row.key);
                        const toggle = (checked: boolean) =>
                          setSkillRows((prev) =>
                            prev.map((r) =>
                              r.key === row.key ? { ...r, enabled: checked, isPrimary: checked ? r.isPrimary : false } : r
                            )
                          );
                        const setPrimary = (on: boolean) =>
                          setSkillRows((prev) =>
                            prev.map((r) => ({ ...r, isPrimary: r.key === row.key ? on : on ? false : r.isPrimary }))
                          );
                        return (
                          <section
                            key={row.key}
                            className={`wsm-card${row.enabled ? ' is-on' : ''}${row.isPrimary ? ' is-primary' : ''}`}
                          >
                            <header className="wsm-card__head">
                              <div className="wsm-card__titles">
                                <span className="wsm-card__name">{row.name}</span>
                                <span className="wsm-card__state">
                                  {row.enabled
                                    ? `Level ${row.proficiency} · ${levelName(row.proficiency)}`
                                    : "Can't run this machine"}
                                </span>
                              </div>
                              <Switch checked={row.enabled} aria-label={`${row.name} enabled`} onChange={toggle} />
                            </header>

                            {row.enabled ? (
                              <div className="wsm-card__controls">
                                <div className="wsm-levels" role="radiogroup" aria-label={`${row.name} level`}>
                                  {[1, 2, 3, 4, 5].map((n) => (
                                    <button
                                      key={n}
                                      type="button"
                                      role="radio"
                                      aria-checked={row.proficiency === n}
                                      aria-label={`Level ${n} ${levelName(n)}`}
                                      className={`wsm-level${row.proficiency === n ? ' is-on' : ''}${
                                        n <= row.proficiency ? ' is-filled' : ''
                                      }`}
                                      onClick={() => setLevel(row.key, n)}
                                    >
                                      {n}
                                    </button>
                                  ))}
                                </div>
                                <button
                                  type="button"
                                  className={`wsm-primary${row.isPrimary ? ' is-on' : ''}`}
                                  aria-pressed={row.isPrimary}
                                  onClick={() => setPrimary(!row.isPrimary)}
                                >
                                  {row.isPrimary ? <StarFilled aria-hidden /> : <StarOutlined aria-hidden />}
                                  Primary
                                </button>
                              </div>
                            ) : null}

                            <div className="wsm-card__record">
                              <SkillRecord ev={ev} />
                              {row.enabled || ev?.suggestedLevel != null ? (
                                <div className="wsm-card__suggest">
                                  <span className="wsm-card__k">Suggested</span>
                                  <SkillSuggestion
                                    ev={ev}
                                    current={row.proficiency}
                                    enabled={row.enabled}
                                    onUse={(level) => setLevel(row.key, level)}
                                  />
                                </div>
                              ) : null}
                            </div>
                          </section>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="ws-skills">
                      <ResponsiveTable
                        card={{ title: 'target', badge: 'enabled' }}
                        size="small"
                        rowKey="key"
                        pagination={false}
                        columns={skillColumns}
                        dataSource={skillRows}
                      />
                    </div>
                  )}
                </div>
              ) : null}

              {detailTab === 'hours' ? (
                <div className="worker-setup-panel">
                  <div className="worker-setup-panel__head">
                    <span>Weekly hours</span>
                    <div className="worker-setup-panel__save m-action-bar">
                      <Button
                        type="primary"
                        loading={savingSchedule}
                        onClick={saveSchedule}
                        style={{ fontWeight: 600 }}
                      >
                        Save hours
                      </Button>
                    </div>
                  </div>
                  {compact ? (
                    <div className="wsm-list wsm-list--days">
                      {schedule.map((row) => {
                        const day = DAY_LABELS[row.dayOfWeek];
                        const hourOptions = Array.from({ length: 24 }, (_, h) => {
                          const v = `${String(h).padStart(2, '0')}:00`;
                          return { value: v, label: `${h % 12 || 12}:00 ${h < 12 ? 'AM' : 'PM'}` };
                        });
                        return (
                          <section key={row.dayOfWeek} className={`wsm-day${row.isWorking ? ' is-on' : ''}`}>
                            <div className="wsm-day__head">
                              <span className="wsm-day__name">{day}</span>
                              {!row.isWorking ? <span className="wsm-day__off">Day off</span> : null}
                              <Switch
                                checked={row.isWorking}
                                aria-label={`${day} working`}
                                onChange={(checked) => {
                                  setSchedule((prev) =>
                                    prev.map((d) =>
                                      d.dayOfWeek === row.dayOfWeek
                                        ? {
                                            ...d,
                                            isWorking: checked,
                                            startTime: checked ? d.startTime || '08:00' : null,
                                            endTime: checked ? d.endTime || '17:00' : null,
                                          }
                                        : d
                                    )
                                  );
                                }}
                              />
                            </div>
                            {row.isWorking ? (
                              <div className="wsm-day__times">
                                <Select
                                  value={row.startTime || undefined}
                                  aria-label={`${day} start`}
                                  options={hourOptions}
                                  onChange={(v) =>
                                    setSchedule((prev) =>
                                      prev.map((d) => (d.dayOfWeek === row.dayOfWeek ? { ...d, startTime: v } : d))
                                    )
                                  }
                                />
                                <span className="wsm-day__to" aria-hidden>
                                  to
                                </span>
                                <Select
                                  value={row.endTime || undefined}
                                  aria-label={`${day} end`}
                                  options={hourOptions}
                                  onChange={(v) =>
                                    setSchedule((prev) =>
                                      prev.map((d) => (d.dayOfWeek === row.dayOfWeek ? { ...d, endTime: v } : d))
                                    )
                                  }
                                />
                              </div>
                            ) : null}
                          </section>
                        );
                      })}
                    </div>
                  ) : (
                    <ResponsiveTable
                      card={{ title: 'dayOfWeek', badge: 'isWorking' }}
                      size="small"
                      rowKey="dayOfWeek"
                      pagination={false}
                      columns={scheduleColumns}
                      dataSource={schedule}
                    />
                  )}
                </div>
              ) : null}

              {detailTab === 'history' ? (
                <WorkerHistoryPanel key={workerId} workerId={workerId} />
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
