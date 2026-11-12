import { Popover, Button } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import { chartPalette, colors } from '../../theme/tokens';
import type { RecommendationWeightSets } from '../../types';
import './recommendationWeights.css';

const WEIGHT_LABEL: Record<string, string> = {
  operator: 'Assigned operator',
  skill: 'Skill',
  efficiency: 'Past performance',
  workload: 'Workload',
};

const WEIGHT_COLOR: Record<string, string> = {
  operator: colors.navy,
  skill: chartPalette[1],
  efficiency: chartPalette[0],
  workload: chartPalette[2],
};

const KIND_LABEL: Record<string, string> = {
  machineLead: 'Machine operation, lead',
  noMachine: 'No machine',
  helper: 'Helper',
};

/** The three weight sets as stacked bars, with what each factor means. */
export function RecommendationWeights({ weights }: { weights?: RecommendationWeightSets | null }) {
  if (!weights) {
    return <div className="rw__note">Add an operation to see how its workers are ranked.</div>;
  }
  return (
    <div className="rw">
      {(['machineLead', 'noMachine', 'helper'] as const).map((kind) => {
        const entries = Object.entries(weights[kind]).sort((a, b) => b[1] - a[1]);
        return (
          <div key={kind}>
            <div className="rw__kind">{KIND_LABEL[kind]}</div>
            <div
              className="rw__bar"
              role="img"
              aria-label={entries.map(([k, v]) => `${WEIGHT_LABEL[k] ?? k} ${Math.round(v * 100)}%`).join(', ')}
            >
              {entries.map(([k, v]) => (
                <span key={k} style={{ width: `${v * 100}%`, background: WEIGHT_COLOR[k] ?? '#94a3b8' }} />
              ))}
            </div>
            <div className="rw__legend">
              {entries.map(([k, v]) => (
                <span key={k} className="rw__item">
                  <span className="rw__swatch" style={{ background: WEIGHT_COLOR[k] ?? '#94a3b8' }} />
                  {WEIGHT_LABEL[k] ?? k} {Math.round(v * 100)}%
                </span>
              ))}
            </div>
          </div>
        );
      })}
      <div className="rw__note">
        Each candidate gets a 0–1 score per factor and the weighted sum ranks them. Past performance is target ÷ hours
        worked (capped at 1.5) on this operation type, or on all types when he has fewer than {weights.minimumSamples}{' '}
        finished. Workload is the share of his own working hours this week that is still free. A machine lead is a worker and a specific unit; units
        with no assigned operator are open to anyone with the skill. Availability is a filter, not a weight: next free
        is the earliest time both the worker and the unit are free.
      </div>
    </div>
  );
}

/** "How workers are recommended" link that opens the weights. */
export default function RecommendationWeightsButton({
  weights,
  compact = false,
}: {
  weights?: RecommendationWeightSets | null;
  compact?: boolean;
}) {
  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      title="How workers are recommended"
      content={<RecommendationWeights weights={weights} />}
    >
      {compact ? (
        <button
          type="button"
          aria-label="How workers are recommended"
          title="How workers are recommended"
          className="ui-infotip"
        >
          <InfoCircleOutlined />
        </button>
      ) : (
        <Button type="link" size="small" icon={<InfoCircleOutlined />} className="rw__trigger">
          How workers are recommended
        </Button>
      )}
    </Popover>
  );
}
