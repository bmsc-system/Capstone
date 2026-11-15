import type { ReactNode } from 'react';
import { Button, Tooltip } from 'antd';
import { ArrowDownOutlined, ArrowUpOutlined, DownloadOutlined, MinusOutlined } from '@ant-design/icons';
import {
  Area,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTip,
  XAxis,
  YAxis,
} from 'recharts';
import InfoTip from '../../components/InfoTip';
import { PHONE_QUERY, useMediaQuery } from '../../hooks/useViewport';
import { chart, chartPalette, colors, tones, type Tone } from '../../theme/tokens';
import { fmtCompact, fmtMonth, fmtNum, symmetricBound } from './format';
import './analytics.css';

/** One palette for every analytics chart, from the design tokens. */
export const PALETTE = {
  primary: chartPalette[0],
  second: chartPalette[1],
  third: chartPalette[2],
  muted: '#94a3b8',
  navy: colors.navy,
  good: tones.green.solid,
  warn: tones.amber.solid,
  bad: tones.red.solid,
  grid: chart.grid,
  axis: chart.axis,
};

export const AXIS_TICK = { fontSize: 12, fill: chart.axis };

export type Span = 3 | 4 | 5 | 6 | 7 | 8 | 12;

// ---------- Layout ----------

export function Grid({ children }: { children: ReactNode }) {
  return <div className="ana-grid">{children}</div>;
}

/** Title, one takeaway line, optional (i), scope chip, controls, CSV export, content. */
export function Card({
  title,
  info,
  takeaway,
  takeawayTone,
  chip,
  controls,
  onExport,
  exportDisabled,
  span = 12,
  foot,
  children,
}: {
  title: string;
  info?: ReactNode;
  takeaway?: ReactNode;
  takeawayTone?: 'good' | 'bad';
  chip?: string;
  controls?: ReactNode;
  onExport?: () => void;
  exportDisabled?: boolean;
  span?: Span;
  foot?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`ana-card ana-span-${span}`}>
      <div className="ana-card__head">
        <div className="ana-card__titles">
          <h3 className="ana-card__title">
            {title}
            {info ? <InfoTip content={info} label={`About ${title}`} /> : null}
          </h3>
          {takeaway ? (
            <div className={`ana-card__take${takeawayTone ? ` is-${takeawayTone}` : ''}`}>{takeaway}</div>
          ) : null}
        </div>
        <div className="ana-card__tools no-print">
          {chip ? <span className="ana-chip">{chip}</span> : null}
          {controls}
          {onExport ? (
            <Tooltip title="Export CSV">
              <Button
                size="small"
                type="text"
                aria-label={`Export ${title} as CSV`}
                icon={<DownloadOutlined />}
                disabled={exportDisabled}
                onClick={onExport}
              />
            </Tooltip>
          ) : null}
        </div>
      </div>
      <div className="ana-card__body">{children}</div>
      {foot ? <div className="ana-card__foot">{foot}</div> : null}
    </section>
  );
}

// ---------- KPIs ----------

export function Kpis({ children }: { children: ReactNode }) {
  return <div className="ana-kpis">{children}</div>;
}

/**
 * Short label, number, and an arrow vs the previous period.
 * ``better`` says which direction is good; ``deltaUnit`` 'pct' is % change, 'pts' is percentage points.
 */
export function Kpi({
  label,
  value,
  unit,
  delta,
  deltaUnit = 'pct',
  better = 'up',
  sub,
  info,
  valueTone,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  delta?: number | null;
  deltaUnit?: 'pct' | 'pts' | 'n';
  better?: 'up' | 'down' | 'none';
  sub?: ReactNode;
  info?: ReactNode;
  valueTone?: Tone;
}) {
  return (
    <div className="ana-kpi">
      <div className="ana-kpi__label">
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
        {info ? <InfoTip content={info} label={`About ${label}`} /> : null}
      </div>
      <div className="ana-kpi__row">
        <span
          className="ana-kpi__value"
          title={typeof value === 'string' || typeof value === 'number' ? String(value) : undefined}
          style={valueTone ? { color: tones[valueTone].fg } : undefined}
        >
          {value}
          {unit ? <small>{unit}</small> : null}
        </span>
        {delta !== undefined ? <Delta value={delta} unit={deltaUnit} better={better} /> : null}
      </div>
      {sub ? (
        <div className="ana-kpi__sub" title={typeof sub === 'string' ? sub : undefined}>
          {sub}
        </div>
      ) : null}
    </div>
  );
}

export function Delta({
  value,
  unit = 'pct',
  better = 'up',
}: {
  value: number | null | undefined;
  unit?: 'pct' | 'pts' | 'n';
  better?: 'up' | 'down' | 'none';
}) {
  if (value == null || !Number.isFinite(value)) {
    return (
      <span className="ana-delta" title="No previous period to compare with">
        <MinusOutlined />
      </span>
    );
  }
  const r = unit === 'n' ? Math.round(value) : Math.round(value);
  const dir = r > 0 ? 'up' : r < 0 ? 'down' : 'flat';
  const tone = dir === 'flat' || better === 'none' ? '' : dir === better ? ' is-good' : ' is-bad';
  const text = unit === 'pct' ? `${Math.abs(r)}%` : unit === 'pts' ? `${Math.abs(r)} pts` : `${Math.abs(r)}`;
  return (
    <span className={`ana-delta${tone}`} title="vs the previous period of the same length">
      {dir === 'up' ? <ArrowUpOutlined /> : dir === 'down' ? <ArrowDownOutlined /> : <MinusOutlined />}
      {dir === 'flat' ? 'same' : text}
    </span>
  );
}

// ---------- Small pieces ----------

export function ChartEmpty({ title, text, height = 140 }: { title?: string; text?: ReactNode; height?: number }) {
  return (
    <div className="ana-empty" style={{ minHeight: height }}>
      {title ? <strong>{title}</strong> : null}
      {text ? <span>{text}</span> : null}
    </div>
  );
}

export function Legend({
  items,
}: {
  items: { label: string; color: string; kind?: 'line' | 'dash' | 'band' | 'bar' | 'dot' }[];
}) {
  return (
    <div className="ana-legend">
      {items.map((i) => (
        <span key={i.label} className="ana-legend__item">
          <span
            className={`ana-legend__swatch${i.kind && i.kind !== 'line' ? ` ana-legend__swatch--${i.kind}` : ''}`}
            style={{ color: i.color }}
          />
          {i.label}
        </span>
      ))}
    </div>
  );
}

type TipRow = { label: string; value: ReactNode };

export function TipBox({ title, rows }: { title: ReactNode; rows: TipRow[] }) {
  return (
    <div className="ana-tip">
      <div className="ana-tip__title">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="ana-tip__row">
          <span>{r.label}</span>
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

// ---------- Forecast line chart ----------

export type ForecastMonth = { month: string; actual: number | null };
export type ForecastPoint = { month: string; forecast: number | null; lower: number | null; upper: number | null };

type ForecastRow = {
  month: string;
  label: string;
  actual?: number | null;
  forecast?: number | null;
  band?: [number, number] | null;
  soFar?: number | null;
  lower?: number | null;
  upper?: number | null;
};

/** Actuals (solid) -> forecast (dashed) with the prediction band; straight lines only. */
export function ForecastChart({
  months,
  horizon,
  soFar,
  format = fmtNum,
  height = 240,
  intervalPct = 80,
  color = PALETTE.primary,
  showLegend = true,
}: {
  months: ForecastMonth[];
  horizon: ForecastPoint[];
  soFar?: number | null;
  format?: (v: number | null | undefined) => string;
  height?: number;
  intervalPct?: number;
  color?: string;
  showLegend?: boolean;
}) {
  const rows: ForecastRow[] = months.map((m) => ({ month: m.month, label: fmtMonth(m.month), actual: m.actual }));
  if (horizon.length && rows.length) {
    const last = rows[rows.length - 1];
    if (last.actual != null) {
      last.forecast = last.actual;
      last.band = [last.actual, last.actual];
    }
  }
  horizon.forEach((h, i) => {
    rows.push({
      month: h.month,
      label: fmtMonth(h.month),
      forecast: h.forecast,
      band: h.lower != null && h.upper != null ? [h.lower, h.upper] : null,
      lower: h.lower,
      upper: h.upper,
      soFar: i === 0 && soFar ? soFar : null,
    });
  });
  const isForecast = (r: ForecastRow) => r.actual == null;

  return (
    <div>
      {showLegend ? (
        <Legend
          items={[
            { label: 'Actual', color },
            { label: 'Forecast', color, kind: 'dash' },
            { label: `${intervalPct}% range`, color, kind: 'band' },
            ...(soFar ? [{ label: 'This month so far', color: PALETTE.muted, kind: 'dot' as const }] : []),
          ]}
        />
      ) : null}
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={PALETTE.grid} vertical={false} />
            <XAxis
              dataKey="label"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: PALETTE.grid }}
              interval="preserveStartEnd"
              minTickGap={18}
            />
            <YAxis
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={44}
              domain={[0, 'auto']}
              allowDecimals={false}
              tickFormatter={(v) => fmtCompact(v)}
            />
            <ChartTip
              cursor={{ stroke: PALETTE.grid }}
              content={({ active, payload }) => {
                const r = active && payload?.[0]?.payload ? (payload[0].payload as ForecastRow) : null;
                if (!r) return null;
                const tipRows: TipRow[] = isForecast(r)
                  ? [
                      { label: 'Forecast', value: format(r.forecast) },
                      ...(r.lower != null ? [{ label: `${intervalPct}% range`, value: `${format(r.lower)} – ${format(r.upper)}` }] : []),
                      ...(r.soFar ? [{ label: 'So far', value: format(r.soFar) }] : []),
                    ]
                  : [{ label: 'Actual', value: format(r.actual) }];
                return <TipBox title={r.label} rows={tipRows} />;
              }}
            />
            <Area
              type="linear"
              dataKey="band"
              stroke="none"
              fill={color}
              fillOpacity={0.14}
              isAnimationActive={false}
              connectNulls
            />
            <Line
              type="linear"
              dataKey="actual"
              stroke={color}
              strokeWidth={2.5}
              dot={{ r: 2.5, fill: color, strokeWidth: 0 }}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
              connectNulls={false}
            />
            <Line
              type="linear"
              dataKey="forecast"
              stroke={color}
              strokeWidth={2}
              strokeDasharray="6 4"
              dot={(p: { cx?: number; cy?: number; payload?: ForecastRow; index?: number }) =>
                p.payload && isForecast(p.payload) && p.cx != null && p.cy != null ? (
                  <circle key={p.index} cx={p.cx} cy={p.cy} r={3.5} fill="#fff" stroke={color} strokeWidth={2} />
                ) : (
                  <g key={p.index} />
                )
              }
              isAnimationActive={false}
            />
            {soFar ? (
              <Line
                type="linear"
                dataKey="soFar"
                stroke="none"
                dot={{ r: 4, fill: '#fff', stroke: PALETTE.muted, strokeWidth: 2 }}
                isAnimationActive={false}
              />
            ) : null}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** "Simple exponential smoothing · MAPE 23% · tested on 8 months" in small text. */
export function MethodNote({
  method,
  metric,
  mae,
  mapePct,
  tested,
  format = fmtNum,
}: {
  method: string | null;
  metric: string | null;
  mae: number | null;
  mapePct: number | null;
  tested: number;
  format?: (v: number | null | undefined) => string;
}) {
  if (!method) return null;
  return (
    <div className="ana-method">
      <span>{method}</span>
      <span>
        {metric === 'MAPE' ? `MAPE ${fmtNum(mapePct, 1)}%` : `MAE ${format(mae)}`} · back-tested on {tested} months
      </span>
    </div>
  );
}

// ---------- Weekly lines ----------

export function LineTrend({
  rows,
  series,
  height = 220,
  yFormat = (v: number) => fmtCompact(v),
  domain,
  ticks,
  zeroLine = false,
  tipFormat,
}: {
  rows: Record<string, number | string | null>[];
  series: { key: string; label: string; color: string; dash?: boolean }[];
  height?: number;
  yFormat?: (v: number) => string;
  domain?: [number, number];
  ticks?: number[];
  zeroLine?: boolean;
  tipFormat?: (key: string, v: number | null) => string;
}) {
  return (
    <div>
      {series.length > 1 ? (
        <Legend items={series.map((s) => ({ label: s.label, color: s.color, kind: s.dash ? 'dash' : 'line' }))} />
      ) : null}
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={PALETTE.grid} vertical={false} />
            <XAxis
              dataKey="label"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: PALETTE.grid }}
              interval="preserveStartEnd"
              minTickGap={18}
            />
            <YAxis
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={44}
              domain={domain ?? [0, 'auto']}
              ticks={ticks}
              allowDecimals={false}
              tickFormatter={yFormat}
            />
            {zeroLine ? <ReferenceLine y={0} stroke={PALETTE.axis} strokeWidth={1} /> : null}
            <ChartTip
              cursor={{ stroke: PALETTE.grid }}
              content={({ active, payload }) => {
                const r = active && payload?.[0]?.payload ? payload[0].payload : null;
                if (!r) return null;
                return (
                  <TipBox
                    title={String(r.tipLabel ?? r.label)}
                    rows={series.map((s) => ({
                      label: s.label,
                      value: tipFormat ? tipFormat(s.key, r[s.key] as number | null) : fmtNum(r[s.key] as number | null),
                    }))}
                  />
                );
              }}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                type="linear"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={2.25}
                strokeDasharray={s.dash ? '6 4' : undefined}
                dot={{ r: 2.5, fill: s.color, strokeWidth: 0 }}
                activeDot={{ r: 4 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---------- Pareto ----------

export type ParetoRow = { label: string; hours: number; cumulativePct: number; shareOfTotalPct: number; count?: number };

function WrapTick({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) {
  const words = String(payload?.value ?? '').replace(' — ', ' ').split(' ');
  const lines: string[] = [];
  let line = '';
  for (const w of words) {
    if ((line + ' ' + w).trim().length > 14 && line) {
      lines.push(line);
      line = w;
    } else {
      line = (line + ' ' + w).trim();
    }
  }
  if (line) lines.push(line);
  return (
    <text x={x} y={(y ?? 0) + 12} textAnchor="middle" fontSize={11.5} fill={PALETTE.axis}>
      {lines.slice(0, 3).map((l, i) => (
        <tspan key={i} x={x} dy={i === 0 ? 0 : 13}>
          {l}
        </tspan>
      ))}
    </text>
  );
}

/** Bars sorted descending, cumulative % line, 80% reference line. */
export function ParetoChart({
  rows,
  threshold = 80,
  vitalFew,
  height = 280,
  valueLabel = 'Hours',
  valueFormat = (v: number) => fmtNum(v, 1),
  vitalLabel = 'top causes',
}: {
  rows: ParetoRow[];
  threshold?: number;
  vitalFew: number;
  height?: number;
  valueLabel?: string;
  valueFormat?: (v: number) => string;
  vitalLabel?: string;
}) {
  // Many bars on a phone: names would overlap, so they move to the tooltip.
  const phone = useMediaQuery(PHONE_QUERY);
  const hideNames = phone && rows.length > 5;
  return (
    <div>
      <Legend
        items={[
          { label: `${valueLabel} (${vitalLabel})`, color: PALETTE.primary, kind: 'bar' },
          { label: valueLabel, color: PALETTE.muted, kind: 'bar' },
          { label: 'Cumulative %', color: PALETTE.navy },
          { label: `${threshold}% line`, color: PALETTE.warn, kind: 'dash' },
        ]}
      />
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={PALETTE.grid} vertical={false} />
            <XAxis
              dataKey="label"
              tick={hideNames ? false : <WrapTick />}
              tickLine={false}
              interval={0}
              height={hideNames ? 6 : 48}
              axisLine={{ stroke: PALETTE.grid }}
            />
            <YAxis
              yAxisId="h"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={40}
              domain={[0, 'auto']}
              tickFormatter={(v) => fmtCompact(v)}
            />
            <YAxis
              yAxisId="pct"
              orientation="right"
              domain={[0, 100]}
              ticks={[0, 20, 40, 60, 80, 100]}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={40}
              tickFormatter={(v) => `${v}%`}
            />
            <ReferenceLine
              yAxisId="pct"
              y={threshold}
              stroke={PALETTE.warn}
              strokeDasharray="5 4"
              strokeWidth={1.5}
            />
            <ChartTip
              cursor={{ fill: 'rgba(37,99,235,0.06)' }}
              content={({ active, payload }) => {
                const r = active && payload?.[0]?.payload ? (payload[0].payload as ParetoRow) : null;
                if (!r) return null;
                return (
                  <TipBox
                    title={r.label}
                    rows={[
                      { label: valueLabel, value: valueFormat(r.hours) },
                      { label: 'Share', value: `${fmtNum(r.shareOfTotalPct, 0)}%` },
                      { label: 'Cumulative', value: `${fmtNum(r.cumulativePct, 0)}%` },
                      ...(r.count != null ? [{ label: 'Times', value: r.count }] : []),
                    ]}
                  />
                );
              }}
            />
            <Bar yAxisId="h" dataKey="hours" radius={[3, 3, 0, 0]} maxBarSize={56} isAnimationActive={false}>
              {rows.map((_, i) => (
                <Cell key={i} fill={i < vitalFew ? PALETTE.primary : PALETTE.muted} />
              ))}
            </Bar>
            <Line
              yAxisId="pct"
              type="linear"
              dataKey="cumulativePct"
              stroke={PALETTE.navy}
              strokeWidth={2}
              dot={{ r: 3, fill: PALETTE.navy, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---------- Sorted bars (HTML) ----------

export type BarRow = {
  key: string;
  label: string;
  value: number;
  display?: string;
  sub?: string;
  color?: string;
  title?: string;
  /** Dark tick at this value, e.g. the stated lead time. */
  marker?: number | null;
};

/** Horizontal bars, largest first. ``reference`` draws a dashed line at that value. */
export function SortedBars({
  rows,
  max,
  reference,
  format = (v: number) => fmtNum(v),
  limit,
  sort = true,
}: {
  rows: BarRow[];
  max?: number;
  reference?: number;
  format?: (v: number) => string;
  limit?: number;
  sort?: boolean;
}) {
  const ordered = (sort ? [...rows].sort((a, b) => b.value - a.value) : rows).slice(0, limit ?? rows.length);
  const top = Math.max(max ?? 0, reference ?? 0, ...ordered.map((r) => Math.max(r.value, r.marker ?? 0)), 1e-9);
  return (
    <div className="ana-bars">
      {ordered.map((r) => (
        <div key={r.key} className="ana-bar" title={r.title}>
          <span className="ana-bar__label" title={r.label}>
            {r.label}
          </span>
          <span className="ana-bar__track">
            <span
              className="ana-bar__fill"
              style={{ width: `${(Math.max(r.value, 0) / top) * 100}%`, background: r.color ?? PALETTE.primary }}
            />
            {reference != null ? <span className="ana-bar__ref" style={{ left: `${(reference / top) * 100}%` }} /> : null}
            {r.marker != null ? <span className="ana-bar__marker" style={{ left: `${(r.marker / top) * 100}%` }} /> : null}
          </span>
          <span className="ana-bar__value">
            {r.display ?? format(r.value)}
            {r.sub ? <small>{r.sub}</small> : null}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Bars left (below 0, good) or right (above 0, bad) of a centre line, e.g. time vs target. */
export function DivergingBars({
  rows,
  format,
  leftLabel,
  rightLabel,
}: {
  rows: { key: string; label: string; value: number; title?: string }[];
  format: (v: number) => string;
  leftLabel: string;
  rightLabel: string;
}) {
  const bound = symmetricBound(rows.map((r) => r.value));
  return (
    <div className="ana-div">
      {rows.map((r) => {
        const w = (Math.min(Math.abs(r.value), bound) / bound) * 50;
        const under = r.value < 0;
        return (
          <div key={r.key} className="ana-div__row" title={r.title}>
            <span className="ana-bar__label" title={r.label}>
              {r.label}
            </span>
            <span className="ana-div__track">
              <span
                className="ana-div__fill"
                style={{
                  width: `${Math.max(w, 0.6)}%`,
                  left: under ? `${50 - w}%` : '50%',
                  background: under ? PALETTE.second : PALETTE.warn,
                }}
              />
            </span>
            <span className="ana-div__val" style={{ color: under ? tones.teal.fg : tones.amber.fg }}>
              {format(r.value)}
            </span>
          </div>
        );
      })}
      <div className="ana-div__axis">
        <span />
        <span>
          <span>
            −{bound}% · {leftLabel}
          </span>
          <span>0</span>
          <span>
            {rightLabel} · +{bound}%
          </span>
        </span>
        <span />
      </div>
    </div>
  );
}

// ---------- Heatmap ----------

const HEAT_STEPS = [
  { min: 75, bg: '#1d4ed8', fg: '#fff' },
  { min: 50, bg: '#3b82f6', fg: '#fff' },
  { min: 25, bg: '#93c5fd', fg: '#0f172a' },
  { min: 5, bg: '#dbeafe', fg: '#1e3a8a' },
  { min: 0.05, bg: '#eff6ff', fg: '#475569' },
  { min: -1, bg: '#f8fafc', fg: '#94a3b8' },
];

export function heatColor(pct: number | null | undefined) {
  const v = pct ?? 0;
  return HEAT_STEPS.find((s) => v >= s.min) ?? HEAT_STEPS[HEAT_STEPS.length - 1];
}

export function Heatmap({
  columns,
  rows,
}: {
  columns: { key: string; label: string }[];
  rows: { key: string; label: string; cells: (number | null)[]; total: number | null; tips?: string[] }[];
}) {
  return (
    <div>
      <div className="ana-heat">
        <table>
          <thead>
            <tr>
              <th />
              {columns.map((c) => (
                <th key={c.key}>{c.label}</th>
              ))}
              <th>Avg</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <th className="ana-heat__machine" scope="row">
                  {r.label}
                </th>
                {r.cells.map((v, i) => {
                  const c = heatColor(v);
                  return (
                    <td key={columns[i].key} style={{ background: c.bg, color: c.fg }} title={r.tips?.[i]}>
                      {v == null ? '' : v < 0.5 ? '·' : Math.round(v)}
                    </td>
                  );
                })}
                <td className="ana-heat__avg">{r.total == null ? '—' : `${Math.round(r.total)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="ana-heat__scale">
        <span>Use %</span>
        {[...HEAT_STEPS].reverse().map((s, i) => (
          <i key={i} style={{ background: s.bg }} />
        ))}
        <span>0 → 75%+</span>
      </div>
    </div>
  );
}

// ---------- Score ring ----------

export const GRADE_TONE: Record<string, Tone> = { A: 'green', B: 'teal', C: 'amber', D: 'red' };

/** The ring from the supplier-score mock: green arc on a light track, number in the middle. */
export function ScoreRing({ score, grade, size = 88 }: { score: number | null; grade: string | null; size?: number }) {
  const stroke = Math.max(7, Math.round(size / 9));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score ?? 0));
  const tone = grade ? tones[GRADE_TONE[grade] ?? 'slate'] : tones.slate;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={score == null ? 'No score' : `Score ${Math.round(pct)} of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e8edf3" strokeWidth={stroke} />
      {score != null ? (
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone.solid}
          strokeWidth={stroke}
          strokeDasharray={`${(pct / 100) * c} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      ) : null}
      <text
        x="50%"
        y="50%"
        dominantBaseline="central"
        textAnchor="middle"
        fontSize={size * 0.27}
        fontWeight={800}
        fill={colors.text}
      >
        {score == null ? '—' : Math.round(pct)}
      </text>
    </svg>
  );
}

export function Grade({ grade }: { grade: string | null }) {
  if (!grade) return null;
  const t = tones[GRADE_TONE[grade] ?? 'slate'];
  return (
    <span className="ana-grade" style={{ background: t.bg, color: t.fg }}>
      {grade}
    </span>
  );
}

// ---------- Restock status ----------

export const RESTOCK_TONE: Record<string, Tone> = {
  OVERDUE: 'red',
  DUE_SOON: 'amber',
  OK: 'green',
  LEARNING: 'grey',
  NO_HISTORY: 'grey',
};

export const RESTOCK_LABEL: Record<string, string> = {
  OVERDUE: 'Reorder now',
  DUE_SOON: 'Reorder soon',
  OK: 'OK',
  LEARNING: 'Too little history',
  NO_HISTORY: 'No history yet',
};

