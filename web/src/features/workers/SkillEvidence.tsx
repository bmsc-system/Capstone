import { Button, Tooltip } from 'antd';
import { ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';
import type { SkillEvidenceRow } from '../../types';
import { formatShop } from '../../utils/shopTime';

/** What each level means, so every Administrator rates the same way. */
export const LEVEL_GUIDE: { level: number; name: string; meaning: string }[] = [
  { level: 1, name: 'Trainee', meaning: 'Works on this machine only as a helper.' },
  { level: 2, name: 'Assisted', meaning: 'Runs simple jobs; someone checks the setup.' },
  { level: 3, name: 'Independent', meaning: 'Sets up and runs standard work alone, within tolerance.' },
  { level: 4, name: 'Advanced', meaning: 'Handles tight tolerances and complex setups; fixes problems himself.' },
  { level: 5, name: 'Expert', meaning: 'Takes the hardest jobs and can train others.' },
];

export function levelName(level: number | null | undefined): string {
  return LEVEL_GUIDE.find((l) => l.level === level)?.name || '';
}

export function LevelGuide() {
  return (
    <div className="ws-guide">
      {LEVEL_GUIDE.map((l) => (
        <div key={l.level} className="ws-guide__row">
          <strong>
            {l.level} · {l.name}
          </strong>
          <span>{l.meaning}</span>
        </div>
      ))}
      <div className="ws-guide__note">
        The record and the suggested level come from the last 12 months of finished operations. They
        are a guide; you decide the level.
      </div>
    </div>
  );
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/** Pace against peers in words; the exact figure is in the tooltip. */
function paceText(pct: number | null) {
  if (pct == null) return null;
  if (pct >= 110) return 'Faster than average';
  if (pct <= 90) return 'Slower than average';
  return 'Average pace';
}

/** Last 12 months on this machine in one short line; the details are in the tooltip. */
export function SkillRecord({ ev }: { ev?: SkillEvidenceRow }) {
  if (!ev || (!ev.leadOps && !ev.helperOps)) {
    return <span className="ws-record ui-muted-note">Not used in 12 months</span>;
  }
  const lastYear = ev.lastDoneAt ? formatShop(ev.lastDoneAt, 'YYYY') : null;
  const last = ev.lastDoneAt
    ? formatShop(ev.lastDoneAt, lastYear === formatShop(new Date().toISOString(), 'YYYY') ? 'MMM D' : 'MMM D, YYYY')
    : null;
  const line = ev.leadOps ? `Led ${ev.leadOps}` : `Helped ${ev.helperOps}`;
  const pace = paceText(ev.speedVsPeersPct);
  const redoBad = Boolean(ev.operatorRedoPct && ev.operatorRedoPct > 10);
  const details = [
    ev.leadOps
      ? `Led ${plural(ev.leadOps, 'operation')}${ev.helperOps ? `, helped on ${ev.helperOps}` : ''}`
      : `Helped on ${plural(ev.helperOps, 'operation')}`,
    ev.lastDoneAt ? `Last used ${formatShop(ev.lastDoneAt, 'MMM D, YYYY')}` : null,
    ev.leadOps && pace ? `Pace: ${pace === 'Average pace' ? 'average' : pace.toLowerCase()}` : null,
    ev.leadOps && ev.onTargetPct != null ? `On target: ${Math.round(ev.onTargetPct)}%` : null,
    ev.leadOps ? `Redone for his own error: ${ev.operatorRedos ? `${ev.operatorRedos}×` : 'none'}` : null,
  ].filter(Boolean);
  return (
    <Tooltip
      title={
        <div>
          {details.map((d) => (
            <div key={d}>{d}</div>
          ))}
        </div>
      }
    >
      <span className="ws-record">
        {line}
        {last ? <span className="ws-record__sub"> · last {last}</span> : null}
        {redoBad ? <ExclamationCircleOutlined className="ws-record__flag" aria-label="Many redos" /> : null}
      </span>
    </Tooltip>
  );
}

/** The suggested level with its reason, a Use button, and any warnings. */
export function SkillSuggestion({
  ev,
  current,
  enabled,
  onUse,
}: {
  ev?: SkillEvidenceRow;
  current: number;
  enabled: boolean;
  onUse: (level: number) => void;
}) {
  const flags: string[] = [];
  if (ev?.suggestedLevel != null && enabled && Math.abs(ev.suggestedLevel - current) >= 2) {
    flags.push(
      `Rated ${current}, but the record suggests ${ev.suggestedLevel}. Consider a ${
        ev.suggestedLevel > current ? 'higher' : 'lower'
      } level.`
    );
  }
  for (const f of ev?.flags || []) {
    if (f.kind === 'unused' && enabled) flags.push(f.text);
  }

  if (ev?.suggestedLevel == null) {
    return (
      <span className="ws-suggest ws-suggest--none">
        <span className="ui-muted-note">—</span>
        {ev?.suggestionReason ? (
          <Tooltip title={ev.suggestionReason}>
            <InfoCircleOutlined className="ws-suggest__info" aria-label={ev.suggestionReason} />
          </Tooltip>
        ) : null}
        {flags.length ? <FlagIcon flags={flags} /> : null}
      </span>
    );
  }
  const same = enabled && ev.suggestedLevel === current;
  return (
    <span className="ws-suggest">
      <Tooltip title={ev.suggestionReason}>
        <span className="ws-suggest__level">
          {ev.suggestedLevel} · {levelName(ev.suggestedLevel)}
        </span>
      </Tooltip>
      {enabled && !same ? (
        <Button size="small" type="link" className="ws-suggest__use" onClick={() => onUse(ev.suggestedLevel!)}>
          Use
        </Button>
      ) : null}
      {flags.length ? <FlagIcon flags={flags} /> : null}
    </span>
  );
}

function FlagIcon({ flags }: { flags: string[] }) {
  return (
    <Tooltip
      title={
        <div>
          {flags.map((f) => (
            <div key={f}>{f}</div>
          ))}
        </div>
      }
    >
      <ExclamationCircleOutlined className="ws-suggest__flag" aria-label={flags.join(' ')} />
    </Tooltip>
  );
}
