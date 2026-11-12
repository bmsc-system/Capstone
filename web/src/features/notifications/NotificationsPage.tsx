import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Skeleton, message } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { EmptyState, Page, SectionCard, StatusPill, TabBar } from '../../components/ui';
import { alertPath } from '../../components/NotificationBell';
import { alertsApi } from '../../api/alerts.api';
import { getErrorMessage } from '../../api/client';
import type { StaffAlert } from '../../types';
import type { Tone } from '../../theme/tokens';
import { formatShop, shopToday } from '../../utils/shopTime';
import './notifications.css';

const PAGE_SIZE = 30;

type Filter = 'all' | 'unread';

/** Short label and colour for each kind of notification. */
const KIND: Record<string, { label: string; tone: Tone }> = {
  OPERATION_DONE: { label: 'Operation finished', tone: 'green' },
  JOB_DONE: { label: 'Job finished', tone: 'green' },
  JOB_AT_RISK: { label: 'At risk', tone: 'red' },
  NEEDS_REPLAN: { label: 'Needs re-plan', tone: 'red' },
  MATERIAL_DELAY: { label: 'Material delay', tone: 'amber' },
  DELIVERY_OVERDUE: { label: 'Late delivery', tone: 'amber' },
  SINGLE_UNIT_DOWN: { label: 'Machine down', tone: 'red' },
  WORK_ON_DOWN_UNIT: { label: 'Machine down', tone: 'red' },
  REPAIR_DATE_PASSED: { label: 'Repair overdue', tone: 'amber' },
  OPERATION_STALLED: { label: 'Stalled', tone: 'amber' },
  WORK_MOVED: { label: 'Work moved', tone: 'blue' },
  LOW_STOCK: { label: 'Running low', tone: 'amber' },
};

/** Today, Yesterday, or the date, for grouping the list. */
function dayLabel(iso: string | null) {
  if (!iso) return 'Earlier';
  const day = formatShop(iso, 'YYYY-MM-DD');
  const today = shopToday();
  if (day === today.format('YYYY-MM-DD')) return 'Today';
  if (day === today.subtract(1, 'day').format('YYYY-MM-DD')) return 'Yesterday';
  return formatShop(iso, day.slice(0, 4) === today.format('YYYY') ? 'dddd, MMM D' : 'MMM D, YYYY');
}

export default function NotificationsPage() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>('all');
  const [items, setItems] = useState<StaffAlert[]>([]);
  const [unread, setUnread] = useState(0);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(
    async (offset: number) => {
      const { data } = await alertsApi.list({ limit: PAGE_SIZE, offset, unread: filter === 'unread' });
      setItems((prev) => (offset ? [...prev, ...data.items] : data.items));
      setUnread(data.unreadCount);
      setTotal(data.total);
      setHasMore(data.hasMore);
    },
    [filter],
  );

  useEffect(() => {
    setLoading(true);
    load(0)
      .catch((err) => message.error(getErrorMessage(err)))
      .finally(() => setLoading(false));
  }, [load]);

  const loadMore = () => {
    setLoadingMore(true);
    load(items.length)
      .catch((err) => message.error(getErrorMessage(err)))
      .finally(() => setLoadingMore(false));
  };

  const open = (a: StaffAlert) => {
    if (!a.read) {
      setItems((prev) => prev.map((x) => (x.id === a.id ? { ...x, read: true } : x)));
      setUnread((c) => Math.max(0, c - 1));
      alertsApi.markRead(a.id).catch(() => undefined);
    }
    const path = alertPath(a);
    if (path) navigate(path);
  };

  const markAll = () => {
    setItems((prev) => (filter === 'unread' ? [] : prev.map((x) => ({ ...x, read: true }))));
    setUnread(0);
    if (filter === 'unread') {
      setTotal(0);
      setHasMore(false);
    }
    alertsApi.markAllRead().catch((err) => message.error(getErrorMessage(err)));
  };

  const groups = useMemo(() => {
    const out: { label: string; items: StaffAlert[] }[] = [];
    for (const a of items) {
      const label = dayLabel(a.createdAt);
      const last = out[out.length - 1];
      if (last && last.label === label) last.items.push(a);
      else out.push({ label, items: [a] });
    }
    return out;
  }, [items]);

  return (
    <Page className="notifs">
      <SectionCard
        flush
        title={unread ? `${unread} unread` : 'All caught up'}
        subtitle="Newest first. Open one to go to its job, order or machine."
        extra={
          <Button onClick={markAll} disabled={!unread}>
            Mark all as read
          </Button>
        }
      >
        <div className="notifs__tabs">
          <TabBar<Filter>
            value={filter}
            onChange={setFilter}
            items={[
              { value: 'all', label: 'All' },
              { value: 'unread', label: 'Unread', count: unread || null, countTone: 'blue' },
            ]}
          />
        </div>

        {loading ? (
          <div className="notifs__empty">
            <Skeleton active title={false} paragraph={{ rows: 6 }} />
          </div>
        ) : !items.length ? (
          <div className="notifs__empty">
            <EmptyState
              icon={<BellOutlined />}
              title={filter === 'unread' ? 'No unread notifications' : 'No notifications yet'}
            />
          </div>
        ) : (
          <>
            {groups.map((g) => (
              <section key={g.label} className="notifs__group">
                <h3 className="notifs__day">{g.label}</h3>
                {g.items.map((a) => {
                  const kind = KIND[a.kind];
                  const clickable = Boolean(alertPath(a));
                  return (
                    <div
                      key={a.id}
                      role="button"
                      tabIndex={0}
                      className={`notifs__item${a.read ? '' : ' is-unread'}${clickable ? '' : ' is-static'}`}
                      onClick={() => open(a)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') open(a);
                      }}
                    >
                      <span className="notifs__dot" aria-label={a.read ? undefined : 'Unread'} />
                      <div className="notifs__body">
                        <div className="notifs__title">{a.title}</div>
                        {a.message ? <div className="notifs__msg">{a.message}</div> : null}
                      </div>
                      <div className="notifs__side">
                        {kind ? (
                          <StatusPill tone={kind.tone} compact>
                            {kind.label}
                          </StatusPill>
                        ) : null}
                        <span className="notifs__time">
                          {a.createdAt ? formatShop(a.createdAt, 'h:mm A') : ''}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </section>
            ))}
            <div className="notifs__more">
              {hasMore ? (
                <Button onClick={loadMore} loading={loadingMore}>
                  Show older
                </Button>
              ) : (
                <span className="ui-muted-note">
                  {total} notification{total === 1 ? '' : 's'}
                </span>
              )}
            </div>
          </>
        )}
      </SectionCard>
    </Page>
  );
}
