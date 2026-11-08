import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Input, Segmented, message } from 'antd';
import {
  DownOutlined,
  ExperimentOutlined,
  InboxOutlined,
  RightOutlined,
  SearchOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { toolsApi } from '../../api/tools.api';
import { consumablesApi } from '../../api/consumables.api';
import { operationsApi } from '../../api/operations.api';
import { getErrorMessage } from '../../api/client';
import { WorkerPageHeader } from '../../layouts/WorkerLayout';
import { EmptyState, PageSkeleton, StatusPill } from '../../components/ui';
import { useOffline } from '../../offline/OfflineProvider';
import { formatShop } from '../../utils/shopTime';
import type { Tone } from '../../theme/tokens';
import type {
  ConsumableReportLevel,
  ToolType,
  ToolUnit,
  ToolUnitStatus,
  WorkerConsumable,
  WorkerJobMaterials,
  WorkerMaterialLine,
} from '../../types';
import { TOOLS_CHANGED, announceToolsChanged } from './toolsEvents';

type Section = 'materials' | 'tools' | 'consumables';
type ToolView = 'holding' | 'all';

const SECTIONS: { value: Section; label: string }[] = [
  { value: 'materials', label: 'Raw materials' },
  { value: 'tools', label: 'Tools' },
  { value: 'consumables', label: 'Consumables' },
];

const UNIT_STATUS: Record<ToolUnitStatus, { label: string; tone: Tone }> = {
  AVAILABLE: { label: 'On rack', tone: 'green' },
  OUT: { label: 'Out', tone: 'amber' },
  UNDER_REPAIR: { label: 'Repair', tone: 'red' },
  RETIRED: { label: 'Retired', tone: 'slate' },
};

function lineStatus(l: WorkerMaterialLine): { label: string; tone: Tone; sub: string | null } {
  if (l.status === 'RECEIVED')
    return { label: 'Arrived', tone: 'green', sub: l.dateReceived ? `Arrived ${formatShop(l.dateReceived, 'MMM D')}` : null };
  if (l.status === 'CONSUMED') return { label: 'In use', tone: 'slate', sub: 'Work on this job has started' };
  if (l.status === 'DRAFT') return { label: 'Being ordered', tone: 'amber', sub: null };
  return {
    label: 'Ordered',
    tone: 'blue',
    sub: l.expectedDate ? `Expected ${formatShop(l.expectedDate, 'MMM D')}` : 'Waiting for delivery',
  };
}

const NO_LINES: Record<string, string> = {
  NOT_REQUIRED: 'No materials to buy (client-supplied or already in the shop).',
  TO_ORDER: 'Materials not ordered yet. The office will order them.',
};

const LEVEL_WORD: Record<ConsumableReportLevel, string> = { LOW: 'running low', OUT: 'out' };

/** Worker inventory: materials for my jobs, tools, and consumables I can report as running low. */
export default function InventoryPage() {
  const navigate = useNavigate();
  const { online } = useOffline();
  const [searchParams] = useSearchParams();
  const askedTab = searchParams.get('tab');
  const [section, setSection] = useState<Section>(() =>
    SECTIONS.some((s) => s.value === askedTab) ? (askedTab as Section) : 'materials'
  );
  // A link such as "Tools you're holding" opens the Tools tab even when the page is kept alive.
  useEffect(() => {
    if (SECTIONS.some((s) => s.value === askedTab)) setSection(askedTab as Section);
  }, [askedTab]);

  const [query, setQuery] = useState('');
  const [toolView, setToolView] = useState<ToolView>('holding');
  const [held, setHeld] = useState<ToolUnit[]>([]);
  const [types, setTypes] = useState<ToolType[]>([]);
  const [toolsLoading, setToolsLoading] = useState(true);
  const [returning, setReturning] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const [materials, setMaterials] = useState<WorkerJobMaterials[] | null>(null);
  const [consumables, setConsumables] = useState<WorkerConsumable[] | null>(null);
  const [busyItem, setBusyItem] = useState<string | null>(null);

  const loadTools = useCallback(async () => {
    try {
      const [h, t] = await Promise.all([toolsApi.myTools(), toolsApi.listTypes({ includeUnits: true })]);
      setHeld(h.data);
      setTypes(t.data);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setToolsLoading(false);
    }
  }, []);

  const loadMaterials = useCallback(async () => {
    try {
      setMaterials((await operationsApi.myMaterials()).data);
    } catch (err) {
      setMaterials([]);
      message.error(getErrorMessage(err));
    }
  }, []);

  const loadConsumables = useCallback(async () => {
    try {
      setConsumables((await consumablesApi.forWorker()).data);
    } catch (err) {
      setConsumables([]);
      message.error(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void loadTools();
    const onChange = () => void loadTools();
    window.addEventListener(TOOLS_CHANGED, onChange);
    return () => window.removeEventListener(TOOLS_CHANGED, onChange);
  }, [loadTools]);

  // Materials and consumables load when their tab is opened (fresh each time).
  useEffect(() => {
    if (!online) return;
    if (section === 'materials') void loadMaterials();
    if (section === 'consumables') void loadConsumables();
  }, [section, online, loadMaterials, loadConsumables]);

  const handleReturn = async (unit: ToolUnit) => {
    setReturning(unit.id);
    try {
      await toolsApi.scan(unit.assetCode, { intent: 'RETURN' });
      message.success(`Returned: ${unit.toolTypeName} (${unit.assetCode})`);
      announceToolsChanged();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setReturning(null);
    }
  };

  const report = async (item: WorkerConsumable, level: ConsumableReportLevel) => {
    if (busyItem) return;
    setBusyItem(item.toolId);
    try {
      await consumablesApi.report(item.toolId, level);
      message.success(`Office told: ${item.name} is ${LEVEL_WORD[level]}`);
      await loadConsumables();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setBusyItem(null);
    }
  };

  const takeBack = async (item: WorkerConsumable) => {
    if (busyItem || !item.report) return;
    setBusyItem(item.toolId);
    try {
      await consumablesApi.withdrawReport(item.report.id);
      message.success(`Report taken back: ${item.name}`);
      await loadConsumables();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setBusyItem(null);
    }
  };

  const q = query.trim().toLowerCase();
  const filteredHeld = useMemo(
    () =>
      q
        ? held.filter((t) => (t.toolTypeName || '').toLowerCase().includes(q) || t.assetCode.toLowerCase().includes(q))
        : held,
    [held, q]
  );
  const filteredTypes = useMemo(
    () =>
      q
        ? types.filter(
            (t) =>
              t.name.toLowerCase().includes(q) ||
              t.code.toLowerCase().includes(q) ||
              (t.units || []).some(
                (u) => u.assetCode.toLowerCase().includes(q) || (u.currentHolderName || '').toLowerCase().includes(q)
              )
          )
        : types,
    [types, q]
  );
  const filteredMaterials = useMemo(
    () =>
      (materials ?? []).filter(
        (j) =>
          !q ||
          `${j.jobNumber} ${j.title} ${j.clientName ?? ''}`.toLowerCase().includes(q) ||
          j.lines.some((l) => `${l.materialName} ${l.gradeOrSpec ?? ''} ${l.sizeSpec ?? ''}`.toLowerCase().includes(q))
      ),
    [materials, q]
  );
  const filteredConsumables = useMemo(
    () =>
      (consumables ?? []).filter(
        (c) => !q || `${c.name} ${c.shopTerm ?? ''} ${c.sizeSpec ?? ''}`.toLowerCase().includes(q)
      ),
    [consumables, q]
  );
  const totalUnits = types.reduce((sum, t) => sum + (t.totalUnits || 0), 0);
  const reportedCount = (consumables ?? []).filter((c) => c.report).length;

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const needConnection = (what: string) => (
    <EmptyState compact icon={<InboxOutlined />} title={`Connect to see ${what}`} />
  );

  // ---------------- Raw materials ----------------
  const renderMaterials = () => {
    if (!online && materials === null) return needConnection('the materials for your jobs');
    if (materials === null) return <PageSkeleton variant="list" />;
    if (filteredMaterials.length === 0)
      return (
        <EmptyState
          compact
          icon={<ExperimentOutlined />}
          title={q ? 'No materials match your search' : 'No open jobs with materials'}
          description={q ? undefined : 'Materials for the jobs assigned to you show here.'}
        />
      );
    return (
      <div className="w-inv-jobs">
        {filteredMaterials.map((job) => (
          <section key={job.jobId} className="w-card w-card--flush">
            <button
              type="button"
              className="w-card__head w-inv-job__head"
              onClick={() => navigate(`/my-assignments/${job.jobId}`)}
            >
              <span className="w-inv-job__titles">
                <span className="w-card__title">{job.jobNumber}</span>
                <span className="w-inv-job__sub">
                  {job.title}
                  {job.clientName ? ` · ${job.clientName}` : ''}
                </span>
              </span>
              <RightOutlined className="w-row__chev" />
            </button>
            {job.lines.length === 0 ? (
              <div className="w-dayoff">{NO_LINES[job.materialStatus ?? ''] ?? 'No material lines yet.'}</div>
            ) : (
              <div className="w-rows">
                {job.lines.map((l) => {
                  const st = lineStatus(l);
                  return (
                    <div key={l.id} className="w-row" style={{ cursor: 'default' }}>
                      <span className="w-row__icon">
                        <ExperimentOutlined />
                      </span>
                      <div className="w-row__main">
                        <div className="w-row__title">
                          {[l.materialName, l.gradeOrSpec].filter(Boolean).join(' — ')}
                        </div>
                        <div className="w-row__sub">
                          {[l.sizeSpec, l.quantity != null ? `${l.quantity} ${l.unit ?? ''}`.trim() : null, st.sub]
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                      </div>
                      <StatusPill tone={st.tone} compact>
                        {st.label}
                      </StatusPill>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        ))}
      </div>
    );
  };

  // ---------------- Tools ----------------
  const renderHolding = () =>
    filteredHeld.length === 0 ? (
      <EmptyState
        compact
        icon={<ToolOutlined />}
        title={q ? 'No tools match your search' : 'No tools out with you'}
        description={q ? undefined : "Scan a tool's QR code to borrow it."}
      />
    ) : (
      <div className="w-card w-card--flush">
        <div className="w-rows">
          {filteredHeld.map((unit) => (
            <div key={unit.id} className="w-row" style={{ cursor: 'default', minHeight: 68 }}>
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
              <Button className="w-action--sm" loading={returning === unit.id} onClick={() => handleReturn(unit)}>
                Return
              </Button>
            </div>
          ))}
        </div>
      </div>
    );

  const renderAllTools = () =>
    filteredTypes.length === 0 ? (
      <EmptyState compact icon={<SearchOutlined />} title="No tools match your search" />
    ) : (
      <div className="w-card w-card--flush">
        <div className="w-rows">
          {filteredTypes.map((type) => {
            const units = (type.units || []).filter((u) => u.status !== 'RETIRED');
            const available = type.availableCount ?? 0;
            const out = type.outCount ?? 0;
            const repair = type.repairCount ?? 0;
            const open = expanded.has(type.id);
            return (
              <div key={type.id} className="w-tooltype">
                <button
                  type="button"
                  className="w-row"
                  style={{ borderTop: 0 }}
                  onClick={() => toggle(type.id)}
                  aria-expanded={open}
                >
                  <span className="w-row__icon">
                    <ToolOutlined />
                  </span>
                  <div className="w-row__main">
                    <div className="w-row__title">{type.name}</div>
                    <div className="w-row__sub">
                      <b style={{ color: available ? 'var(--tone-green-fg)' : 'var(--c-text-muted)' }}>
                        {available} available
                      </b>
                      {out ? ` · ${out} out` : ''}
                      {repair ? ` · ${repair} in repair` : ''}
                    </div>
                  </div>
                  {open ? <DownOutlined className="w-row__chev" /> : <RightOutlined className="w-row__chev" />}
                </button>
                {open && (
                  <div className="w-units">
                    {units.length === 0 ? (
                      <div className="w-unit__who">No active units</div>
                    ) : (
                      units.map((u) => {
                        const st = UNIT_STATUS[u.status] || { label: u.status, tone: 'slate' as Tone };
                        return (
                          <div key={u.id} className="w-unit">
                            <div style={{ minWidth: 0 }}>
                              <div className="w-unit__code">{u.assetCode}</div>
                              {u.status === 'OUT' && (
                                <div className="w-unit__who">
                                  {u.currentHolderName ? `With ${u.currentHolderName}` : 'Out'}
                                </div>
                              )}
                            </div>
                            <StatusPill tone={st.tone} compact>
                              {st.label}
                            </StatusPill>
                          </div>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );

  const renderTools = () => (
    <>
      <div className="w-chips" role="tablist" aria-label="Which tools" style={{ marginBottom: 10 }}>
        <button
          type="button"
          role="tab"
          aria-selected={toolView === 'holding'}
          className={`w-chip${toolView === 'holding' ? ' is-active' : ''}`}
          onClick={() => setToolView('holding')}
        >
          Holding ({held.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={toolView === 'all'}
          className={`w-chip${toolView === 'all' ? ' is-active' : ''}`}
          onClick={() => setToolView('all')}
        >
          All tools ({totalUnits})
        </button>
      </div>
      {toolsLoading ? <PageSkeleton variant="list" /> : toolView === 'holding' ? renderHolding() : renderAllTools()}
    </>
  );

  // ---------------- Consumables ----------------
  const renderConsumables = () => {
    if (!online && consumables === null) return needConnection('the consumables');
    if (consumables === null) return <PageSkeleton variant="list" />;
    if (filteredConsumables.length === 0)
      return <EmptyState compact icon={<SearchOutlined />} title={q ? 'No consumables match your search' : 'No consumables yet'} />;
    return (
      <>
        <div className="w-inv-hint">
          Running out of something? Tap <b>Running low</b> or <b>Out</b> and the office is told to buy it.
        </div>
        <div className="w-card w-card--flush">
          <div className="w-rows">
            {filteredConsumables.map((c) => {
              const r = c.report;
              const busy = busyItem === c.toolId;
              return (
                <div key={c.toolId} className="w-row w-inv-item" style={{ cursor: 'default' }}>
                  <div className="w-inv-item__top">
                    <span className="w-row__icon">
                      <InboxOutlined />
                    </span>
                    <div className="w-row__main">
                      <div className="w-row__title">{c.name}</div>
                      <div className="w-row__sub">
                        {[c.sizeSpec, c.shopTerm ? `“${c.shopTerm}”` : null, c.unit].filter(Boolean).join(' · ')}
                      </div>
                    </div>
                    {r ? (
                      <StatusPill tone={r.level === 'OUT' ? 'red' : 'amber'} compact>
                        {r.level === 'OUT' ? 'Out' : 'Running low'}
                      </StatusPill>
                    ) : null}
                  </div>
                  {r ? (
                    <div className="w-inv-item__note">
                      {r.mine ? 'You reported it' : `Reported by ${r.reportedByName ?? 'a worker'}`}
                      {r.reportedAt ? ` · ${formatShop(r.reportedAt, 'MMM D, h:mm A')}` : ''}. The office has been told.
                    </div>
                  ) : null}
                  <div className="w-inv-item__actions">
                    {!r ? (
                      <Button className="w-inv-btn" disabled={!online || busy} loading={busy} onClick={() => report(c, 'LOW')}>
                        Running low
                      </Button>
                    ) : null}
                    {!r || r.level === 'LOW' ? (
                      <Button
                        className="w-inv-btn"
                        danger
                        disabled={!online || busy}
                        loading={busy && !!r}
                        onClick={() => report(c, 'OUT')}
                      >
                        Out
                      </Button>
                    ) : null}
                    {r?.mine ? (
                      <Button className="w-inv-btn" disabled={!online || busy} onClick={() => takeBack(c)}>
                        Take back
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </>
    );
  };

  const subtitle =
    section === 'tools'
      ? `${held.length} with you · ${totalUnits} tool units`
      : section === 'materials'
        ? materials
          ? `${materials.length} open job${materials.length === 1 ? '' : 's'}`
          : undefined
        : consumables
          ? `${consumables.length} items${reportedCount ? ` · ${reportedCount} reported` : ''}`
          : undefined;

  const placeholder =
    section === 'tools'
      ? toolView === 'all'
        ? 'Search tools or holder'
        : 'Search your tools'
      : section === 'materials'
        ? 'Search job or material'
        : 'Search consumables';

  return (
    <div>
      <WorkerPageHeader title="Inventory" subtitle={subtitle} />
      <div className="w-page">
        <div className="w-toolbar">
          <Segmented
            block
            className="worker-seg"
            value={section}
            onChange={(v) => setSection(v as Section)}
            options={SECTIONS}
          />
          <Input
            allowClear
            className="worker-search"
            prefix={<SearchOutlined style={{ color: 'var(--c-text-muted)' }} />}
            placeholder={placeholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {section === 'materials' ? renderMaterials() : section === 'tools' ? renderTools() : renderConsumables()}
      </div>
    </div>
  );
}
