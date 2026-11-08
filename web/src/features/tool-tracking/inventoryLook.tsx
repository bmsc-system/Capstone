import type { LucideIcon } from 'lucide-react';
import {
  Droplets,
  Flame,
  HardHat,
  Package,
  Scissors,
  Sparkles,
} from 'lucide-react';
import type { ReactNode } from 'react';
import StatCard, { StatGrid } from '../../components/ui/StatCard';
import { MOBILE_SHELL_QUERY, useMediaQuery } from '../../hooks/useViewport';

export type InventoryCardTone = 'slate' | 'green' | 'amber' | 'red' | 'blue';

export interface InventorySummaryCard {
  key: string;
  label: string;
  /** Used in the compact phone/tablet strip. */
  shortLabel?: string;
  value: number;
  icon: LucideIcon;
  tone?: InventoryCardTone;
  caption?: string;
}

const TONE: Record<InventoryCardTone, { bg: string; fg: string; tile: string }> = {
  slate: { bg: '#f8fafc', fg: '#0f172a', tile: '#e2e8f0' },
  green: { bg: '#f0fdf4', fg: '#166534', tile: '#bbf7d0' },
  amber: { bg: '#fffbeb', fg: '#b45309', tile: '#fde68a' },
  red: { bg: '#fef2f2', fg: '#b91c1c', tile: '#fecaca' },
  blue: { bg: '#eff6ff', fg: '#1d4ed8', tile: '#bfdbfe' },
};

/** Category → small rounded icon tile (materials + consumable families). */
export function categoryIcon(category: string | null | undefined): {
  Icon: LucideIcon;
  tile: string;
  fg: string;
} {
  const c = (category || '').toLowerCase();
  if (c.includes('steel') || c.includes('iron') || c.includes('cast')) {
    return { Icon: Flame, tile: '#e2e8f0', fg: '#334155' };
  }
  if (c.includes('aluminium') || c.includes('aluminum')) {
    return { Icon: Sparkles, tile: '#e0f2fe', fg: '#0369a1' };
  }
  if (c.includes('non-ferrous') || c.includes('brass') || c.includes('bronze') || c.includes('copper')) {
    return { Icon: Sparkles, tile: '#fef3c7', fg: '#a16207' };
  }
  if (c.includes('plastic')) {
    return { Icon: Package, tile: '#f3e8ff', fg: '#7e22ce' };
  }
  if (c.includes('abras') || c.includes('sand') || c.includes('disc') || c.includes('flap')) {
    return { Icon: Scissors, tile: '#ffe4e6', fg: '#be123c' };
  }
  if (c.includes('weld')) {
    return { Icon: Flame, tile: '#ffedd5', fg: '#c2410c' };
  }
  if (c.includes('cut') || c.includes('drill') || c.includes('mill') || c.includes('tap') || c.includes('insert')) {
    return { Icon: Scissors, tile: '#e0e7ff', fg: '#4338ca' };
  }
  if (c.includes('fluid') || c.includes('oil') || c.includes('grease') || c.includes('coolant')) {
    return { Icon: Droplets, tile: '#cffafe', fg: '#0e7490' };
  }
  if (c.includes('ppe') || c.includes('glove') || c.includes('safety')) {
    return { Icon: HardHat, tile: '#dcfce7', fg: '#15803d' };
  }
  return { Icon: Package, tile: '#f1f5f9', fg: '#475569' };
}

export function CategoryTile({
  category,
  size = 28,
  icon,
}: {
  category?: string | null;
  size?: number;
  icon?: LucideIcon;
}) {
  const { Icon: CategoryIcon, tile, fg } = categoryIcon(category);
  const Icon = icon ?? CategoryIcon;
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        borderRadius: 8,
        background: tile,
        color: fg,
        flexShrink: 0,
      }}
    >
      <Icon size={Math.round(size * 0.55)} strokeWidth={2} aria-hidden />
    </span>
  );
}

export function InventorySummaryCards({
  cards,
  activeKey,
  onSelect,
}: {
  cards: InventorySummaryCard[];
  activeKey?: string | null;
  onSelect: (key: string | null) => void;
}) {
  const compact = useMediaQuery(MOBILE_SHELL_QUERY);
  if (compact) {
    return (
      <div className="inventory-summary-cards--compact">
        <StatGrid min={150}>
          {cards.map((card) => {
            const pressed = activeKey === card.key;
            const Icon = card.icon;
            return (
              <StatCard
                key={card.key}
                icon={<Icon size={16} strokeWidth={2} aria-hidden />}
                tone={card.tone || 'slate'}
                valueTone={card.tone && card.tone !== 'slate' && card.value > 0 ? card.tone : undefined}
                label={card.shortLabel ?? card.label}
                value={card.value}
                hint={card.caption}
                active={pressed}
                onClick={() => onSelect(pressed ? null : card.key)}
              />
            );
          })}
        </StatGrid>
      </div>
    );
  }
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${Math.max(4, Math.min(cards.length, 5))}, minmax(0, 1fr))`,
        gap: 10,
        marginBottom: 14,
      }}
      className={`inventory-summary-cards${cards.length > 4 ? ' inventory-summary-cards--five' : ''}`}
    >
      <style>{`
        @media (max-width: 1279.98px) {
          .inventory-summary-cards--five { grid-template-columns: repeat(3, minmax(0, 1fr)) !important; }
        }
        @media (max-width: 900px) {
          .inventory-summary-cards { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
        }
        @media (max-width: 480px) {
          .inventory-summary-cards { grid-template-columns: 1fr !important; }
        }
      `}</style>
      {cards.map((card) => {
        const tone = TONE[card.tone || 'slate'];
        const pressed = activeKey === card.key;
        const Icon = card.icon;
        return (
          <button
            key={card.key}
            type="button"
            aria-pressed={pressed}
            onClick={() => onSelect(pressed ? null : card.key)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              textAlign: 'left',
              padding: '12px 14px',
              borderRadius: 12,
              border: pressed ? `2px solid ${tone.fg}` : '1px solid #e2e8f0',
              background: tone.bg,
              color: tone.fg,
              cursor: 'pointer',
              minHeight: 72,
            }}
          >
            <span
              aria-hidden
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: tone.tile,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <Icon size={20} strokeWidth={2} aria-hidden />
            </span>
            <span style={{ minWidth: 0 }}>
              <span style={{ display: 'block', fontSize: 22, fontWeight: 800, lineHeight: 1.1 }}>
                {card.value}
              </span>
              <span style={{ display: 'block', fontSize: 12, fontWeight: 600, opacity: 0.9 }}>
                {card.label}
              </span>
              {card.caption ? (
                <span style={{ display: 'block', fontSize: 11, opacity: 0.7, marginTop: 2 }}>
                  {card.caption}
                </span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Seed badge is a temporary placeholder until the real asset list arrives. */
export function showSeedBadge(): boolean {
  return !import.meta.env.PROD;
}

export function InventoryEmptyState({
  icon: Icon,
  title,
  action,
}: {
  icon: LucideIcon;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div
      style={{
        padding: '36px 16px',
        textAlign: 'center',
        color: '#64748b',
      }}
    >
      <Icon size={36} strokeWidth={1.5} aria-hidden style={{ marginBottom: 10, opacity: 0.7 }} />
      <div style={{ fontSize: 14, marginBottom: action ? 12 : 0 }}>{title}</div>
      {action}
    </div>
  );
}
