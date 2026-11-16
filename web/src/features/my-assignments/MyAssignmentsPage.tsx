import { useEffect, useMemo, useState } from 'react';
import { Input, Segmented, Select } from 'antd';
import { ProfileOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { WorkerPageHeader } from '../../layouts/WorkerLayout';
import SyncChip from '../../offline/SyncChip';
import { EmptyState, PageSkeleton, StatusPill } from '../../components/ui';
import { opStatusPill } from './opStatus';
import { useMyOperations } from './useMyOperations';
import {
  SORT_OPTIONS,
  filterChips,
  filterOps,
  sortOps,
  whenLine,
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
          shown.map((op) => {
            const pill = opStatusPill(op);
            const line = whenLine(op);
            const job = [op.jobNumber || op.jobOrderId.slice(0, 8).toUpperCase(), op.clientName || op.jobTitle]
              .filter(Boolean)
              .join(' · ');
            return (
              <button
                key={op.id}
                type="button"
                className="w-card w-card--flush w-job"
                onClick={() => navigate(`/my-assignments/${op.jobOrderId}`)}
              >
                <div className="w-job__head">
                  <span className="w-job__name">{op.operationName || op.name || 'Operation'}</span>
                  <span className="w-job__pills">
                    {op.syncState && <SyncChip state={op.syncState} />}
                    {/* Every card on the Completed tab is completed: no pill there. */}
                    {tab === 'active' ? (
                      <StatusPill tone={pill.tone} compact>
                        {pill.label}
                      </StatusPill>
                    ) : null}
                  </span>
                </div>
                <div className="w-job__client">{job}</div>
                {line ? <div className="w-job__when">{line}</div> : null}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
