import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import StatusPill from '../../components/StatusPill';
import { PageSkeleton, TabBar } from '../../components/ui';
import { tones } from '../../theme/tokens';
import { exportCsv } from '../../utils/csvExport';
import type { ReplenishmentItem } from '../../types';
import { fmtDay, fmtMonth, fmtNum, fmtPeso, fmtPesoCompact } from './format';
import {
  Card,
  ChartEmpty,
  Grid,
  Kpi,
  Kpis,
  LineTrend,
  PALETTE,
  RESTOCK_LABEL,
  RESTOCK_TONE,
  SortedBars,
  type BarRow,
} from './kit';
import { restockCounts } from './cards';
import { useConsumables, useInventory, usePeriods } from './sources';

const STATUS_ORDER = ['OVERDUE', 'DUE_SOON', 'OK', 'LEARNING', 'NO_HISTORY'] as const;
const LIST_LIMIT = 10;
const NEXT_LIMIT = 10;

const itemName = (name: string, size?: string | null) => (size ? `${name} · ${size}` : name);
const statusColor = (status: string) => tones[RESTOCK_TONE[status] ?? 'grey'].solid;
const isDue = (i: ReplenishmentItem) => i.status === 'OVERDUE' || i.status === 'DUE_SOON';
const nextLabel = (i: ReplenishmentItem) =>
  i.daysUntilNext == null
    ? '—'
    : i.daysUntilNext < 0
      ? `${fmtNum(-i.daysUntilNext)} d late`
      : i.daysUntilNext === 0
        ? 'today'
        : `in ${fmtNum(i.daysUntilNext)} d`;

type InvView = 'consumables' | 'materials';

export default function AnalyticsInventoryPage() {
  const { period } = usePeriods();
  const [params, setParams] = useSearchParams();
  const view: InvView = params.get('view') === 'materials' ? 'materials' : 'consumables';
  const setView = (next: InvView) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (next === 'consumables') out.delete('view');
        else out.set('view', next);
        return out;
      },
      { replace: true },
    );
  const stock = useConsumables().data;
  const inv = useInventory(period).data;

  const counts = restockCounts(stock);
  const items = useMemo(() => stock?.items ?? [], [stock]);

  // Coming purchases, soonest first; overdue ones are in the To buy table.
  const nextRows: BarRow[] = useMemo(
    () =>
      items
        .filter((i) => i.daysUntilNext != null && i.daysUntilNext >= 0)
        .sort((a, b) => (a.daysUntilNext ?? 0) - (b.daysUntilNext ?? 0))
        .slice(0, NEXT_LIMIT)
        .map((i) => ({
          key: i.toolId,
          label: itemName(i.name, i.sizeSpec),
          value: Math.max(i.daysUntilNext ?? 0, 0),
          display: nextLabel(i),
          color: statusColor(i.status),
          title: `${RESTOCK_LABEL[i.status]} · bought every ~${fmtNum(i.everyDays)} days`,
        })),
    [items],
  );

  const everyRows: BarRow[] = useMemo(
    () =>
      items
        .filter((i) => i.everyDays != null)
        .sort((a, b) => (a.everyDays ?? 0) - (b.everyDays ?? 0))
        .slice(0, NEXT_LIMIT)
        .map((i) => ({
          key: i.toolId,
          label: itemName(i.name, i.sizeSpec),
          value: i.everyDays ?? 0,
          display: `${fmtNum(i.everyDays)} d`,
          sub: i.usualQuantity != null ? `about ${fmtNum(i.usualQuantity)} ${i.unit} each time` : undefined,
        })),
    [items],
  );

  const toBuy = useMemo(() => items.filter(isDue), [items]);

  if (!stock || !inv) return <PageSkeleton variant="cards" />;

  const raw = inv.rawMaterials;
  const due = counts.overdue + counts.dueSoon;
  const errors = items.map((i) => i.typicalErrorDays).filter((v): v is number => v != null);
  const typicalError = errors.length ? errors.reduce((s2, v) => s2 + v, 0) / errors.length : null;
  const spendRows = stock.summary.spendByMonth.map((m) => ({ label: fmtMonth(m.month), spent: m.amount }));
  const statusCounts = STATUS_ORDER.map((k) => ({ k, n: items.filter((i) => i.status === k).length }));
  const statusRows: BarRow[] = statusCounts
    .filter((r) => r.n > 0)
    .map((r) => ({ key: r.k, label: RESTOCK_LABEL[r.k], value: r.n, color: statusColor(r.k) }));
  const usedRows: BarRow[] = raw.mostUsed.map((m) => ({
    key: m.material,
    label: m.material,
    value: m.jobs,
    display: `${fmtNum(m.jobs)} job${m.jobs === 1 ? '' : 's'}`,
    sub: m.quantity,
    color: PALETTE.primary,
  }));
  const deliveryRows: BarRow[] = raw.deliveryByMaterial.map((m) => ({
    key: m.material,
    label: m.material,
    value: m.avgDays,
    display: `${fmtNum(m.avgDays, 1)} d`,
    sub: `${m.deliveries} deliver${m.deliveries === 1 ? 'y' : 'ies'}${m.late ? ` · ${m.late} late` : ''}`,
    color: m.late ? PALETTE.warn : PALETTE.second,
  }));

  const materialsGrid = (
      <Grid>
        <Card
          title="Most used raw materials"
          span={6}
          takeaway={
            raw.mostUsed.length
              ? `${raw.mostUsed[0].material} was needed by the most jobs in this period`
              : undefined
          }
          info={
            <div style={{ maxWidth: 340 }}>
              How many job orders bought each raw material in the period, with the total quantity.
              Materials needed often are worth keeping a little of, or asking the supplier for a better price.
            </div>
          }
        >
          {usedRows.length ? (
            <SortedBars rows={usedRows} sort={false} />
          ) : (
            <ChartEmpty title="No raw materials bought in this period" />
          )}
        </Card>

        <Card
          title="Delivery time by raw material"
          span={6}
          takeaway={
            raw.avgDeliveryDays != null
              ? `Raw materials took ${fmtNum(raw.avgDeliveryDays, 1)} days on average from order to delivery`
              : undefined
          }
          info={
            <div style={{ maxWidth: 340 }}>
              Days from the purchase order to delivery, slowest first. Order the slow ones as soon as
              the job order comes in. Late means delivered after the date the supplier promised.
            </div>
          }
        >
          {deliveryRows.length ? (
            <SortedBars rows={deliveryRows} sort={false} format={(v) => `${fmtNum(v, 1)} d`} />
          ) : (
            <ChartEmpty title="No raw materials delivered in this period" />
          )}
        </Card>

        <Card
          title="Jobs waiting for raw materials"
          span={12}
          takeaway={
            raw.waitingJobs
              ? `${fmtNum(raw.waitingJobs)} job${raw.waitingJobs === 1 ? '' : 's'} cannot start until these arrive`
              : undefined
          }
          controls={
            <Link to="/tools?tab=raw-materials" className="ana-muted" style={{ fontSize: 12 }}>
              All raw materials
            </Link>
          }
        >
          {raw.waiting.length ? (
            <div className="ana-table__wrap">
              <table className="ana-table">
                <thead>
                  <tr>
                    <th>Raw material</th>
                    <th>For job</th>
                    <th className="num">Qty</th>
                    <th>Supplier</th>
                    <th>Expected</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {raw.waiting.map((r) => (
                    <tr key={r.id}>
                      <td className="strong">
                        {r.material}
                        {r.sizeSpec ? (
                          <span className="ana-muted" style={{ fontWeight: 400 }}>
                            {' '}
                            · {r.sizeSpec}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <Link to={`/job-orders/${r.jobOrderId}`}>{r.jobNumber}</Link>
                      </td>
                      <td className="num">
                        {fmtNum(r.quantity)} <span className="ana-muted">{r.unit}</span>
                      </td>
                      <td>{r.supplierName || '—'}</td>
                      <td>{r.expectedDate ? fmtDay(r.expectedDate) : '—'}</td>
                      <td>
                        {r.daysLate > 0 ? (
                          <StatusPill tone="red" compact>
                            {fmtNum(r.daysLate)} day{r.daysLate === 1 ? '' : 's'} late
                          </StatusPill>
                        ) : (
                          <StatusPill tone="blue" compact>
                            On order
                          </StatusPill>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ChartEmpty title="No job is waiting for raw materials" />
          )}
        </Card>
      </Grid>
  );

  const switcher = (
    <div className="ana-inv__switch">
      <TabBar<InvView>
        value={view}
        onChange={setView}
        items={[
          { value: 'consumables', label: 'Consumables', count: due || null, countTone: 'red' },
          { value: 'materials', label: 'Raw materials', count: raw.waitingJobs || null, countTone: raw.waitingLate ? 'red' : undefined },
        ]}
      />
      <span className="ana-inv__switch-sub">
        {view === 'consumables'
          ? `Forecast as of ${fmtDay(stock.asOf)} from what the shop bought · consumables are not counted`
          : 'Bought for each job order: which are needed most, how long they take to arrive, and which jobs are still waiting'}
      </span>
    </div>
  );

  if (view === 'materials') {
    return (
      <div className="ana-inv">
        {switcher}
        <Kpis>
          <Kpi
            label="Raw materials bought"
            value={fmtNum(raw.boughtLines)}
            sub={`for ${fmtNum(raw.boughtJobs)} job${raw.boughtJobs === 1 ? '' : 's'} · ${fmtPesoCompact(raw.boughtValue)}`}
          />
          <Kpi
            label="Most used"
            value={raw.mostUsed[0]?.material ?? '—'}
            sub={raw.mostUsed[0] ? `needed by ${fmtNum(raw.mostUsed[0].jobs)} job${raw.mostUsed[0].jobs === 1 ? '' : 's'}` : 'Nothing bought in this period'}
          />
          <Kpi
            label="Delivery time"
            value={raw.avgDeliveryDays != null ? `${fmtNum(raw.avgDeliveryDays, 1)} days` : '—'}
            sub={raw.onTimePct != null ? `${fmtNum(raw.onTimePct)}% came by the promised date` : 'No deliveries in this period'}
          />
          <Kpi
            label="Jobs waiting for raw materials"
            value={fmtNum(raw.waitingJobs)}
            valueTone={raw.waitingLate ? 'red' : undefined}
            sub={raw.waitingLate ? `${fmtNum(raw.waitingLate)} deliver${raw.waitingLate === 1 ? 'y' : 'ies'} late` : 'None late'}
          />
        </Kpis>
        {materialsGrid}
      </div>
    );
  }

  return (
    <div className="ana-inv">
      {switcher}
      <Kpis>
        <Kpi
          label="Consumables to buy"
          value={fmtNum(due)}
          valueTone={counts.overdue ? 'red' : due ? 'amber' : 'green'}
          sub={`${fmtNum(counts.overdue)} overdue · ${fmtNum(counts.dueSoon)} due within ${stock.method.dueSoonDays} days`}
        />
        <Kpi
          label="Spent · last 30 days"
          value={fmtPesoCompact(stock.summary.spentLast30Days)}
          sub="Recorded purchases and received orders"
        />
        <Kpi
          label="Items with a forecast"
          value={`${fmtNum(stock.summary.forecast)} of ${fmtNum(items.length)}`}
          sub={`Needs ${stock.method.minPurchases} purchases`}
        />
        <Kpi
          label="Typical forecast error"
          value={typicalError == null ? '—' : `± ${fmtNum(typicalError, 1)} d`}
          sub="Predicted vs actual days between purchases"
        />
      </Kpis>
      <Grid>
        <Card
          title="Coming purchases"
          span={7}
          takeaway={
            nextRows.length
              ? `The ${nextRows.length} due to be bought next${counts.overdue ? ` · ${counts.overdue} overdue are in the To buy list` : ''}`
              : undefined
          }
          info={
            <div style={{ maxWidth: 380 }}>
              Days until each item is usually bought again: the last purchase plus the smoothed
              number of days between purchases ({stock.method.name}, smoothing{' '}
              {fmtNum(stock.method.alpha, 1)}). Red: the date has passed.
            </div>
          }
        >
          {nextRows.length ? (
            <SortedBars rows={nextRows} sort={false} />
          ) : (
            <ChartEmpty
              title="No forecast yet"
              text={`An item is forecast after ${stock.method.minPurchases} recorded purchases.`}
            />
          )}
        </Card>

        <Card title="Status" span={5} takeaway={`${fmtNum(items.length)} consumables in the catalog`}>
          {statusRows.length ? <SortedBars rows={statusRows} sort={false} /> : <ChartEmpty title="No consumables yet" />}
        </Card>

        <Card
          title="Spent per month"
          span={6}
          takeaway={`${fmtPesoCompact(stock.summary.spendByMonth.reduce((s2, m) => s2 + m.amount, 0))} on consumables in the last ${stock.summary.spendByMonth.length} months`}
        >
          {stock.summary.spendByMonth.some((m) => m.amount > 0) ? (
            <LineTrend
              rows={spendRows}
              series={[{ key: 'spent', label: 'Consumables bought (₱)', color: PALETTE.primary }]}
              yFormat={(v) => fmtPesoCompact(v)}
              tipFormat={(_k, v) => fmtPeso(v)}
              height={220}
            />
          ) : (
            <ChartEmpty title="No purchases recorded yet" />
          )}
        </Card>

        <Card title="How often each is bought" span={6} takeaway="Smoothed days between purchases">
          {everyRows.length ? (
            <SortedBars rows={everyRows} sort={false} />
          ) : (
            <ChartEmpty title="No forecast yet" />
          )}
        </Card>

        <Card
          title="To buy"
          span={12}
          takeaway={
            toBuy.length
              ? `${toBuy.length} due or overdue${toBuy.length > LIST_LIMIT ? ` · showing the ${LIST_LIMIT} most overdue` : ''}`
              : 'Nothing due to be bought'
          }
          takeawayTone={toBuy.length ? 'bad' : 'good'}
          controls={
            <Link to="/tools?tab=consumables" className="ana-muted" style={{ fontSize: 12 }}>
              All consumables
            </Link>
          }
          onExport={() =>
            exportCsv(`consumables-${stock.asOf}`, items, [
              { key: 'n', header: 'Item', value: (r) => r.name },
              { key: 's', header: 'Size', value: (r) => r.sizeSpec ?? '' },
              { key: 'u', header: 'Unit', value: (r) => r.unit },
              { key: 'p', header: 'Purchases', value: (r) => r.purchases },
              { key: 'l', header: 'Last bought', value: (r) => r.lastBoughtOn ?? '' },
              { key: 'e', header: 'Every (days)', value: (r) => r.everyDays ?? '' },
              { key: 'q', header: 'Usual quantity', value: (r) => r.usualQuantity ?? '' },
              { key: 'm', header: 'Monthly need', value: (r) => r.monthlyNeed ?? '' },
              { key: 'x', header: 'Next purchase', value: (r) => r.nextPurchaseOn ?? '' },
              { key: 'err', header: 'Typical error (days)', value: (r) => r.typicalErrorDays ?? '' },
              { key: 'st', header: 'Status', value: (r) => RESTOCK_LABEL[r.status] },
            ])
          }
        >
          {toBuy.length ? (
            <div className="ana-table__wrap">
              <table className="ana-table">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Last bought</th>
                    <th className="num">Every</th>
                    <th className="num">Usual qty</th>
                    <th>Next purchase</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {toBuy.slice(0, LIST_LIMIT).map((i) => (
                    <tr key={i.toolId}>
                      <td className="strong">
                        {i.name}
                        {i.sizeSpec ? (
                          <span className="ana-muted" style={{ fontWeight: 400 }}>
                            {' '}
                            · {i.sizeSpec}
                          </span>
                        ) : null}
                      </td>
                      <td>{fmtDay(i.lastBoughtOn)}</td>
                      <td className="num">{fmtNum(i.everyDays)} d</td>
                      <td className="num">
                        {fmtNum(i.usualQuantity)} <span className="ana-muted">{i.unit}</span>
                      </td>
                      <td>
                        {fmtDay(i.nextPurchaseOn)} <span className="ana-muted">({nextLabel(i)})</span>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <StatusPill tone={RESTOCK_TONE[i.status]} compact>
                          {RESTOCK_LABEL[i.status]}
                        </StatusPill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ChartEmpty title="Nothing to buy" text="No consumable is due within the next week." />
          )}
        </Card>
      </Grid>
    </div>
  );
}
