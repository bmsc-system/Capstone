import { useEffect, useMemo, useRef, useState } from 'react';
import { Input, Segmented, Select } from 'antd';
import { CaretRightFilled, ClockCircleOutlined, ProfileOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { WorkerPageHeader } from '../../layouts/WorkerLayout';
import SyncChip from '../../offline/SyncChip';
import { EmptyState, PageSkeleton, StatusPill } from '../../components/ui';
import { useOffline } from '../../offline/OfflineProvider';
import { formatHoursMinutes } from '../../utils/duration';
import type { Operation } from '../../types';
import { machineLabel, opStatusPill } from './opStatus';
import { useMyOperations } from './useMyOperations';
import { announceRecorded, operationActionLabel, showActionError } from './operationActions';
import {
  SORT_OPTIONS,
  activeWhen,
  dateTile,
  filterChips,
  filterOps,
  groupOps,
  sortOps,
  type AssignmentSort,
  type AssignmentTab,
} from './assignmentList';

export default function MyAssignmentsPage() {
  const [tab, setTab] = useState<AssignmentTab>('active');
  const [query, setQuery] = useState('');
  const [chip, setChip] = useState('all');
  const [sort, setSort] = useState<AssignmentSort>('newest');
  const navigate = useNavigate();
  const { operations, loading, error } = useMyOperations();
  const { online, record } = useOffline();
  const [resuming, setResuming] = useState<string | null>(null);
  // A ref, not only state: a second tap can arrive before the re-render.
  const actingRef = useRef(false);

  const resume = async (op: Operation) => {
    if (actingRef.current) return;
    actingRef.current = true;
    setResuming(op.id);
    try {
      const outcome = await record({
        kind: 'resume',
        label: operationActionLabel(op, 'Resume', op.jobNumber),
        operationId: op.id,
        jobOrderId: op.jobOrderId,
      });
      announceRecorded(outcome, online, 'Operation resumed');
    } catch (err) {
      showActionError(err);
    } finally {
      setResuming(null);
      window.setTimeout(() => {
        actingRef.current = false;
      }, 1500);
    }
  };

  const active = useMemo(() => operations.filter((o) => o.status !== 'COMPLETED'), [operations]);
  const completed = useMemo(() => operations.filter((o) => o.status === 'COMPLETED'), [operations]);
  const source = tab === 'active' ? active : completed;
  const chips = useMemo(() => filterChips(source, tab), [source, tab]);

  // A chip or sort that does not fit the other tab goes back to the default.
  useEffect(() => {
    setChip('all');
    if (!SORT_OPTIONS[tab].some((o) => o.value === sort)) setSort('newest');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const searched = q
      ? source.filter((o) =>
          [o.operationName, o.name, o.jobTitle, o.clientName, o.jobNumber, o.machineTypeName, o.machineUnitLabel].some(
            (v) => (v || '').toLowerCase().includes(q)
          )
        )
      : source;
    const key = chips.some((c) => c.key === chip) ? chip : 'all';
    return sortOps(filterOps(searched, tab, key), tab, sort);
  }, [source, query, chip, chips, tab, sort]);

  return (
    <div>
      <WorkerPageHeader
        title="Assigned jobs"
        subtitle={loading ? undefined : `${active.length} active · ${completed.length} completed`}
      />

      <div className="w-page">
        <div className="w-toolbar">
          <Segmented
            block
            className="worker-seg"
            value={tab}
            onChange={(v) => setTab(v as AssignmentTab)}
            options={[
              { label: `Active (${active.length})`, value: 'active' },
              { label: `Completed (${completed.length})`, value: 'completed' },
            ]}
          />
          <div className="w-job-tools">
            <Input
              allowClear
              className="worker-search"
              prefix={<SearchOutlined style={{ color: 'var(--c-text-muted)' }} />}
              placeholder="Search jobs"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Select
              className="w-job-sort"
              aria-label="Sort"
              value={sort}
              onChange={(v) => setSort(v)}
              options={SORT_OPTIONS[tab]}
              popupMatchSelectWidth={false}
            />
          </div>
          {chips.length > 2 || tab === 'completed' ? (
            <div className="w-chips" role="tablist" aria-label="Filter">
              {chips.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  role="tab"
                  aria-selected={chip === c.key}
                  className={`w-chip${chip === c.key ? ' is-active' : ''}`}
                  onClick={() => setChip(c.key)}
                >
                  {c.label} · {c.count}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {loading ? (
          <PageSkeleton variant="cards" />
        ) : error ? (
          <div className="w-card" style={{ color: 'var(--tone-red-fg)', fontSize: 14 }}>
            {error}
          </div>
        ) : shown.length === 0 ? (
          <EmptyState
            compact
            icon={query ? <SearchOutlined /> : <ProfileOutlined />}
            title={
              query || chip !== 'all'
                ? 'No matching jobs'
                : tab === 'completed'
                  ? 'No finished operations yet'
                  : 'No active operations'
            }
            description={!query && chip === 'all' && tab === 'active' ? 'New assignments show up here.' : undefined}
          />
        ) : (
          groupOps(shown, tab).map((group) => (
            <section key={group.key} className="w-jobs">
              <div className="w-jobs__head">
                <span>{group.label}</span>
                {group.note ? <span>{group.note}</span> : null}
              </div>
              {group.ops.map((op) => {
                const pill = opStatusPill(op);
                const tile = dateTile(op);
                const machine = machineLabel(op);
                const when = tab === 'active' ? activeWhen(op) : null;
                const paused = op.status === 'IN_PROGRESS' && op.isPaused;
                return (
                  <div key={op.id} className={`w-job2 w-job2--${pill.tone}`}>
                    <button
                      type="button"
                      className="w-job2__main"
                      onClick={() => navigate(`/my-assignments/${op.jobOrderId}`)}
                    >
                      <span className="w-job2__tile" aria-hidden="true">
                        <b>{tile?.day ?? '—'}</b>
                        {tile ? <i>{tile.label}</i> : null}
                      </span>
                      <span className="w-job2__body">
                        <span className="w-job2__head">
                          <span className="w-job2__name">{op.operationName || op.name || 'Operation'}</span>
                          <span className="w-job__pills">
                            {op.syncState && <SyncChip state={op.syncState} />}
                            {/* Every card on the Completed tab is completed: no pill there. */}
                            {tab === 'active' ? (
                              <StatusPill tone={pill.tone} compact>
                                {pill.label}
                              </StatusPill>
                            ) : null}
                          </span>
                        </span>
                        <span className="w-job2__jo">
                          <code>{op.jobNumber || op.jobOrderId.slice(0, 8).toUpperCase()}</code>
                          <span>{op.clientName || op.jobTitle}</span>
                        </span>
                        {when || machine ? (
                          <span className="w-job2__meta">
                            {when ? (
                              <span>
                                <ClockCircleOutlined /> {when}
                              </span>
                            ) : null}
                            {machine ? (
                              <span>
                                <ToolOutlined /> {machine}
                              </span>
                            ) : null}
                          </span>
                        ) : null}
                      </span>
                      {tab === 'completed' && op.actualWorkedHours != null ? (
                        <span className="w-job2__hours">
                          <b>{formatHoursMinutes(op.actualWorkedHours)}</b>
                          <i>worked</i>
                        </span>
                      ) : null}
                    </button>
                    {paused ? (
                      <button
                        type="button"
                        className="w-job2__cta"
                        disabled={resuming === op.id}
                        onClick={() => resume(op)}
                      >
                        <CaretRightFilled /> {resuming === op.id ? 'Resuming…' : 'Tap to resume'}
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </section>
          ))
        )}
      </div>
    </div>
  );
}
