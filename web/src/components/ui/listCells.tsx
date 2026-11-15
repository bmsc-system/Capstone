import { Tag, Tooltip } from 'antd';
import type { ReactNode } from 'react';

/** Single-line text that truncates with a tooltip of the full value. */
export function EllipsisText({
  children,
  title,
  className,
}: {
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  const tip =
    title ??
    (typeof children === 'string' || typeof children === 'number' ? String(children) : undefined);
  return (
    <Tooltip title={tip || undefined} mouseEnterDelay={0.35}>
      <span className={`ui-ellipsis${className ? ` ${className}` : ''}`}>{children}</span>
    </Tooltip>
  );
}

/** Name + muted secondary line; both ellipsis; fits fixed table rows. */
export function TwoLineCell({
  primary,
  secondary,
}: {
  primary: ReactNode;
  secondary?: ReactNode | null;
}) {
  return (
    <span className="ui-cell-stack">
      <EllipsisText className="ui-cell-stack__primary">{primary}</EllipsisText>
      {secondary ? (
        <EllipsisText className="ui-cell-stack__secondary">{secondary}</EllipsisText>
      ) : null}
    </span>
  );
}

/** At most `max` chips on one line, then a "+N" chip with a tooltip of the rest. */
export function ChipOverflow({
  items,
  max = 2,
  renderChip,
  plain = false,
}: {
  items: string[];
  max?: number;
  renderChip?: (item: string) => ReactNode;
  /** Comma-separated text instead of chips. */
  plain?: boolean;
}) {
  if (!items.length) return <span>—</span>;
  if (plain) {
    const shownText = items.slice(0, max).join(', ');
    const restText = items.slice(max);
    return (
      <span className="ui-chip-text">
        {shownText}
        {restText.length > 0 ? (
          <Tooltip title={restText.join(', ')} mouseEnterDelay={0.2}>
            <span className="ui-chip-text__more"> +{restText.length}</span>
          </Tooltip>
        ) : null}
      </span>
    );
  }
  const shown = items.slice(0, max);
  const rest = items.slice(max);
  const chip = renderChip ?? ((g: string) => <Tag key={g}>{g}</Tag>);
  return (
    <span className="ui-chip-row">
      {shown.map((g) => (
        <span key={g} className="ui-chip-row__item">
          {chip(g)}
        </span>
      ))}
      {rest.length > 0 ? (
        <Tooltip title={rest.join(', ')} mouseEnterDelay={0.2}>
          <Tag className="ui-chip-row__more">+{rest.length}</Tag>
        </Tooltip>
      ) : null}
    </span>
  );
}

const AVATAR_TONE = {
  blue: { bg: '#e0ecff', fg: '#1d4ed8' },
  teal: { bg: '#d9f3ee', fg: '#0f766e' },
} as const;

/** Round icon at the start of a row, so a list reads as people or companies. */
export function RowAvatar({
  icon,
  tone = 'blue',
  size = 36,
}: {
  icon: ReactNode;
  tone?: keyof typeof AVATAR_TONE;
  size?: number;
}) {
  const t = AVATAR_TONE[tone];
  return (
    <span
      aria-hidden
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: t.bg,
        color: t.fg,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      {icon}
    </span>
  );
}
