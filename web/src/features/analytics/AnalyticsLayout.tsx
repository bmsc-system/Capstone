import { shopToday } from '../../utils/shopTime';
import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Button, DatePicker, Select, Tooltip } from 'antd';
import { PrinterOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { PageSkeleton, TabBar } from '../../components/ui';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { PHONE_QUERY, useMediaQuery } from '../../hooks/useViewport';
import {
  AnalyticsPeriodProvider,
  defaultAnalyticsRange,
  type AnalyticsRange,
} from './analyticsPeriod';
import './analytics.css';

/** Tabs follow objective 4: describe (Overview, Production), diagnose (Delays),
 *  predict (Sales & demand, Inventory), prescribe (Suppliers). Worker analytics
 *  live under Production (Admin only). */
const ADMIN_TABS = [
  { label: 'Overview', value: '/analytics' },
  { label: 'Production', value: '/analytics/production' },
  { label: 'Delays', value: '/analytics/delays' },
  { label: 'Sales & demand', value: '/analytics/sales' },
  { label: 'Inventory', value: '/analytics/inventory' },
  { label: 'Suppliers', value: '/analytics/suppliers' },
  { label: 'Reports', value: '/analytics/reports' },
];

const OFFICE_TABS = [
  { label: 'Overview', value: '/analytics' },
  { label: 'Sales & demand', value: '/analytics/sales' },
  { label: 'Inventory', value: '/analytics/inventory' },
  { label: 'Suppliers', value: '/analytics/suppliers' },
  { label: 'Reports', value: '/analytics/reports' },
];

const PRESETS = [
  { label: 'Last 4 weeks', value: () => [shopToday().subtract(4, 'week'), shopToday()] },
  { label: 'Last 8 weeks', value: () => [shopToday().subtract(8, 'week'), shopToday()] },
  { label: 'Last 3 months', value: () => [shopToday().subtract(3, 'month'), shopToday()] },
  { label: 'This year', value: () => [shopToday().startOf('year'), shopToday()] },
].map((p) => ({ label: p.label, value: p.value() as [dayjs.Dayjs, dayjs.Dayjs] }));

const sameDays = (a: dayjs.Dayjs, b: dayjs.Dayjs) => a.format('YYYY-MM-DD') === b.format('YYYY-MM-DD');
const CUSTOM = 'custom';

export default function AnalyticsLayout() {
  const [range, setRange] = useState<AnalyticsRange>(defaultAnalyticsRange);
  const navigate = useNavigate();
  const location = useLocation();
  const tabs = useAuth().user?.role === 'ADMIN' ? ADMIN_TABS : OFFICE_TABS;

  const active = useMemo(
    () =>
      tabs.find((t) => t.value !== '/analytics' && location.pathname.startsWith(t.value))?.value ??
      tabs[0].value,
    [location.pathname, tabs]
  );
  const noPeriod = active === '/analytics/reports';
  const phone = useMediaQuery(PHONE_QUERY);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [customOpen, setCustomOpen] = useState(false);

  // Phone: the tabs are one scrolling row; keep the current tab in view.
  useEffect(() => {
    if (!phone) return;
    toolbarRef.current
      ?.querySelector('.ant-segmented-item-selected')
      ?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [phone, active]);

  const presetIndex = PRESETS.findIndex((p) => sameDays(p.value[0], range[0]) && sameDays(p.value[1], range[1]));
  const periodValue = customOpen || presetIndex < 0 ? CUSTOM : String(presetIndex);
  const rangeLabel = `${range[0].format('MMM D')} – ${range[1].format('MMM D, YYYY')}`;

  const rangePicker = (
    <DatePicker.RangePicker
      value={range}
      allowClear={false}
      format="MMM D, YYYY"
      aria-label="Period"
      presets={phone ? undefined : PRESETS}
      classNames={phone ? { popup: { root: 'ana-range-pop' } } : undefined}
      inputReadOnly={phone}
      onChange={(vals) => {
        if (vals?.[0] && vals?.[1]) {
          setRange([vals[0].startOf('day'), vals[1].endOf('day')]);
        }
      }}
      disabledDate={(d) => d.isAfter(shopToday(), 'day')}
    />
  );

  return (
    <AnalyticsPeriodProvider range={range} setRange={setRange}>
      <div className="ui-page ui-page--stack analytics-page ana">
        <div ref={toolbarRef} className="ui-card analytics-page__toolbar ana-toolbar no-print">
          <TabBar<string>
            items={tabs.map((t) => ({ label: t.label, value: t.value }))}
            value={active}
            onChange={(v) => navigate(v)}
          />
          {!noPeriod && phone && (
            <div className="ana-toolbar__right ana-period">
              <Select
                className="ana-period__select"
                value={periodValue}
                aria-label="Period"
                popupMatchSelectWidth
                options={[
                  ...PRESETS.map((p, i) => ({ value: String(i), label: p.label })),
                  { value: CUSTOM, label: periodValue === CUSTOM ? `Custom · ${rangeLabel}` : 'Custom range…' },
                ]}
                labelRender={({ value }) =>
                  value === CUSTOM ? (
                    rangeLabel
                  ) : (
                    <span>
                      {PRESETS[Number(value)]?.label}
                      <span className="ana-period__dates"> · {rangeLabel}</span>
                    </span>
                  )
                }
                onChange={(v) => {
                  if (v === CUSTOM) {
                    setCustomOpen(true);
                    return;
                  }
                  setCustomOpen(false);
                  const [from, to] = PRESETS[Number(v)].value;
                  setRange([from.startOf('day'), to.endOf('day')]);
                }}
              />
              <Tooltip title="Print this tab">
                <Button icon={<PrinterOutlined />} aria-label="Print this tab" onClick={() => window.print()} />
              </Tooltip>
            </div>
          )}
          {!noPeriod && phone && periodValue === CUSTOM ? <div className="ana-period__custom">{rangePicker}</div> : null}
          {!noPeriod && !phone && <div className="ana-toolbar__spacer" aria-hidden />}
          {!noPeriod && !phone && (
            <div className="ana-toolbar__right">
              {rangePicker}
              <Tooltip title="Print this tab">
                <Button icon={<PrinterOutlined />} aria-label="Print this tab" onClick={() => window.print()} />
              </Tooltip>
            </div>
          )}
        </div>
        <Suspense fallback={<PageSkeleton />}>
          <Outlet />
        </Suspense>
      </div>
    </AnalyticsPeriodProvider>
  );
}
