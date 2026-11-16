import { formatShop } from '../../utils/shopTime';
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { DatePicker, Pagination, Skeleton, Tooltip, Typography, message } from 'antd';
import { WarningOutlined } from '@ant-design/icons';
import { EmptyState } from '../../components/ui';
import { type Dayjs } from 'dayjs';
import { workerProfileApi } from '../../api/users.api';
import { getErrorMessage } from '../../api/client';
import type { WorkerHistoryOperation, WorkerWorkHistory } from '../../types';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { HISTORY_PAGE_SIZE, historyRangeLabel } from './workerHistoryPaging';

const { Text } = Typography;

type Props = { workerId: string };

export default function WorkerHistoryPanel({ workerId }: Props) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<WorkerWorkHistory | null>(null);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null]>([null, null]);
  const [page, setPage] = useState(1);
  const [heldPage, setHeldPage] = useState(1);
  const [eventPage, setEventPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const { data: hist } = await workerProfileApi.getHistory(workerId, {
          from: range[0]?.format('YYYY-MM-DD'),
          to: range[1]?.format('YYYY-MM-DD'),
          page,
          perPage: HISTORY_PAGE_SIZE,
        });
        if (!cancelled) setData(hist);
      } catch (err) {
        if (!cancelled) message.error(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workerId, range, page]);

  if (loading && !data) {
    return <Skeleton active paragraph={{ rows: 6 }} title={false} />;
  }

  if (!data) {
    return (
      <EmptyState
        compact
        icon={<WarningOutlined />}
        title="Could not load history"
        description="Pick the worker again to retry."
      />
    );
  }

  const s = data.summary;
  const pct = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`);
  const ranged = Boolean(range[0] || range[1]);
  const total = s.operationsTotal ?? data.operations.total;
  const hoursRatio =
    s.enoughHistory && s.totalEstimatedHours && s.totalActualHours != null
      ? Math.min(s.totalActualHours / s.totalEstimatedHours, 2)
      : null;
  const varianceTone =
    !s.enoughHistory || s.averageVariancePct == null
      ? ''
      : s.averageVariancePct > 5
        ? ' is-bad'
        : s.averageVariancePct < -5
          ? ' is-good'
          : ' is-ok';

  return (
    <div className="wh">
      <header className="wh-head">
        <div className="wh-head__text">
          <h3 className="wh-head__title">Work history</h3>
          <span className="wh-head__sub">
            {total} operation{total === 1 ? '' : 's'} completed
            {ranged ? ' in this range' : ''}
          </span>
        </div>
        <DatePicker.RangePicker
          className="wh-head__range"
          format="MMM D, YYYY"
          allowClear
          value={range}
          placeholder={['From', 'To']}
          onChange={(vals) => {
            setRange([vals?.[0] ?? null, vals?.[1] ?? null]);
            setPage(1);
          }}
        />
      </header>

      <section className="wh-score" aria-label="Summary">
        <div className="wh-score__item">
          <span className="wh-score__label">Completed</span>
          <span className="wh-score__value">{total}</span>
          <span className="wh-score__hint">
            operations
          </span>
        </div>
        <div className="wh-score__item wh-score__item--wide">
          <span className="wh-score__label">Hours · target → actual</span>
          <span className="wh-score__value">
            {s.enoughHistory ? (
              <>
                {s.totalEstimatedHours ?? '—'}
                <span className="wh-score__arrow">→</span>
                {s.totalActualHours ?? '—'}
                <small>h</small>
              </>
            ) : (
              '—'
            )}
          </span>
          {hoursRatio != null ? (
            <span className="wh-meter" title="Actual hours against target hours">
              <span
                className={`wh-meter__fill${hoursRatio > 1.05 ? ' is-bad' : ''}`}
                style={{ width: `${Math.min(hoursRatio, 1) * 100}%` }}
              />
              {hoursRatio > 1 ? (
                <span className="wh-meter__over" style={{ width: `${Math.min(hoursRatio - 1, 1) * 100}%` }} />
              ) : null}
            </span>
          ) : (
            <span className="wh-score__hint">needs more history</span>
          )}
        </div>
        <div className="wh-score__item">
          <span className="wh-score__label">Avg vs target</span>
          <span className={`wh-score__value${varianceTone}`}>
            {s.enoughHistory ? pct(s.averageVariancePct) : '—'}
          </span>
          <span className="wh-score__hint">first-time operations</span>
        </div>
        <div className="wh-score__item">
          <span className="wh-score__label">Close to target</span>
          <span className="wh-score__value">
            {s.enoughHistory && s.onEstimateRatePct != null ? `${s.onEstimateRatePct}%` : '—'}
          </span>
          <span className="wh-score__hint">of operations</span>
        </div>
      </section>
      {s.message ? <Text type="secondary" className="wh-note">{s.message}</Text> : null}

      <div className="wh-body">
        <section className="wh-card wh-card--ops">
          <div className="wh-card__title">Completed operations</div>
          <ResponsiveTable<WorkerHistoryOperation>
            className="wh-ops"
            size="small"
            rowKey="id"
            loading={loading}
            dataSource={data.operations.items}
            card={{ title: 'jobNumber', subtitle: ['completedAt'] }}
            onRow={(record) => ({
              onClick: () => {
                if (record.jobOrderId) navigate(`/job-orders/${record.jobOrderId}`);
              },
            })}
            pagination={{
              current: data.operations.page,
              total: data.operations.total,
              pageSize: HISTORY_PAGE_SIZE,
              size: 'small',
              onChange: (p) => setPage(p),
              showSizeChanger: false,
              showTotal: (total, span) =>
                historyRangeLabel(total, span[0] > 0 ? Math.floor((span[0] - 1) / HISTORY_PAGE_SIZE) + 1 : 1),
            }}
            locale={{ emptyText: 'No completed operations in this range' }}
            columns={[
              {
                title: 'Date',
                dataIndex: 'completedAt',
                width: 76,
                render: (v: string | null) => (v ? formatShop(v, 'MMM D') : '—'),
              },
              {
                title: 'Job',
                dataIndex: 'jobNumber',
                width: 120,
                onCell: () => ({ className: 'wh-job-cell' }),
                onHeaderCell: () => ({ className: 'wh-job-cell' }),
                render: (v: string | null, r) =>
                  r.jobOrderId ? (
                    <Link
                      to={`/job-orders/${r.jobOrderId}`}
                      className="wh-jobno"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {v || '—'}
                    </Link>
                  ) : (
                    <span className="wh-jobno">{v || '—'}</span>
                  ),
              },
              {
                title: 'Operation',
                key: 'op',
                render: (_: unknown, r) => (
                  <span className="wh-op">
                    <span className="wh-op__name">{r.operationName}</span>
                    {r.machineUnitLabel ? <span className="wh-op__machine">{r.machineUnitLabel}</span> : null}
                    {r.isCrew ? (
                      <Tooltip title={(r.crew || []).map((m) => m.fullName).join(', ')}>
                        <span className="wh-tag">
                          {r.crewRole === 'LEAD' ? 'Lead' : 'Helper'} · {r.crewSize} workers
                        </span>
                      </Tooltip>
                    ) : null}
                  </span>
                ),
              },
              {
                title: 'Target',
                dataIndex: 'estimatedHours',
                width: 66,
                align: 'right',
                render: (v: number | null) => (v == null ? '—' : `${v.toFixed(1)}h`),
              },
              {
                title: 'Actual',
                dataIndex: 'actualHours',
                width: 66,
                align: 'right',
                render: (v: number | null) => (v == null ? '—' : `${v.toFixed(1)}h`),
              },
              {
                title: 'Diff',
                dataIndex: 'differenceHours',
                width: 66,
                align: 'right',
                render: (v: number | null) => {
                  if (v == null) return '—';
                  const cls = v > 0.05 ? 'is-bad' : v < -0.05 ? 'is-good' : '';
                  const shown = Math.abs(v) < 0.05 ? '0.0' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`;
                  return <span className={`wh-diff ${cls}`}>{shown}</span>;
                },
              },
            ]}
          />
        </section>

        <aside className="wh-side">
          <ToolPanel
            title="Tools held now"
            empty="None right now"
            items={data.toolsHeld}
            page={heldPage}
            onPage={setHeldPage}
            renderItem={(u) => (
              <li key={u.id} className="wh-tool-row">
                <span className="wh-tool-row__dot is-held" aria-hidden />
                <span className="wh-tool-row__text">
                  <strong>{u.toolTypeName || 'Tool'}</strong>
                  <span className="wh-tool-row__meta">
                    {u.assetCode}
                    {u.heldSince ? ` · since ${formatShop(u.heldSince, 'MMM D')}` : ''}
                  </span>
                </span>
              </li>
            )}
          />
          <ToolPanel
            title="Recent borrow & return"
            empty="No borrow history"
            items={data.toolEvents}
            page={eventPage}
            onPage={setEventPage}
            renderItem={(ev) => (
              <li key={ev.id} className="wh-tool-row">
                <span className={`wh-tool-row__dot ${ev.type === 'RETURN' ? 'is-in' : 'is-out'}`} aria-hidden />
                <span className="wh-tool-row__text">
                  <strong>
                    {ev.type === 'RETURN' ? 'Returned' : 'Borrowed'} {ev.toolName || 'tool'}
                  </strong>
                  <span className="wh-tool-row__meta">
                    {[ev.assetCode, ev.createdAt ? formatShop(ev.createdAt, 'MMM D, h:mm A') : null]
                      .filter(Boolean)
                      .join(' · ') || '—'}
                  </span>
                </span>
              </li>
            )}
          />
        </aside>
      </div>
    </div>
  );
}

function ToolPanel<T extends { id: string }>({
  title,
  empty,
  items,
  page,
  onPage,
  renderItem,
}: {
  title: string;
  empty: string;
  items: T[];
  page: number;
  onPage: (page: number) => void;
  renderItem: (item: T) => ReactNode;
}) {
  const start = (page - 1) * HISTORY_PAGE_SIZE;
  const slice = items.slice(start, start + HISTORY_PAGE_SIZE);
  return (
    <section className="wh-card wh-tool-panel">
      <div className="wh-card__title">
        {title}
        {items.length ? <span className="wh-card__count">{items.length}</span> : null}
      </div>
      {items.length === 0 ? (
        <div className="wh-tool-panel__body wh-tool-panel__empty">{empty}</div>
      ) : (
        <ul className="wh-tool-panel__body">{slice.map(renderItem)}</ul>
      )}
      {items.length > HISTORY_PAGE_SIZE ? (
        <Pagination
          className="wh-pager"
          size="small"
          current={page}
          pageSize={HISTORY_PAGE_SIZE}
          total={items.length}
          showSizeChanger={false}
          showTotal={(total, span) =>
            historyRangeLabel(total, span[0] > 0 ? Math.floor((span[0] - 1) / HISTORY_PAGE_SIZE) + 1 : 1)
          }
          onChange={onPage}
        />
      ) : null}
    </section>
  );
}
