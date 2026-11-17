import { useEffect, useMemo, useState } from 'react';
import { Alert, Input, Spin, message } from 'antd';
import {
  ArrowRightOutlined,
  CalendarOutlined,
  CheckCircleFilled,
  SearchOutlined,
  UserSwitchOutlined,
} from '@ant-design/icons';
import { getErrorMessage } from '../../api/client';
import { operationsApi } from '../../api/operations.api';
import { FormModal } from '../../components/ui';
import LevelBar from '../../components/ui/LevelBar';
import { PersonAvatar } from '../../components/PersonAvatar';
import type { Operation, OperationReplacements } from '../../types';
import { personLabel } from '../../utils/people';
import { formatShop } from '../../utils/shopTime';
import { levelName } from '../workers/SkillEvidence';
import './changeWorker.css';

type Person = {
  workerId: string;
  fullName: string;
  nickname?: string | null;
  photoVersion?: number | null;
  level?: number | null;
  /** Weighted score as a percentage. */
  match?: number | null;
  /** Share of the week still free, as a percentage. */
  free?: number | null;
  warning?: string | null;
  best?: boolean;
  helping?: boolean;
};

const pct = (v?: number | null) => (v == null ? null : Math.round(v * 100));

/** "Thu, Nov 19 · 2:30 – 3:45 PM", or both dates when it runs past one day. */
function bookedText(from: string, to: string) {
  const sameDay = formatShop(from, 'YYYY-MM-DD') === formatShop(to, 'YYYY-MM-DD');
  return sameDay
    ? `${formatShop(from, 'ddd, MMM D')} · ${formatShop(from, 'h:mm')} – ${formatShop(to, 'h:mm A')}`
    : `${formatShop(from, 'MMM D, h:mm A')} – ${formatShop(to, 'MMM D, h:mm A')}`;
}

function PersonRow({ p, selected, onPick }: { p: Person; selected: boolean; onPick: () => void }) {
  const level = p.level ? Math.round(p.level) : null;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className={`cw-row${selected ? ' is-selected' : ''}`}
      onClick={onPick}
    >
      <PersonAvatar userId={p.workerId} fullName={p.fullName} photoVersion={p.photoVersion} size={36} />
      <span className="cw-row__main">
        <span className="cw-row__name">
          <span className="cw-row__text">{personLabel(p.fullName, p.nickname)}</span>
          {p.best && <span className="cw-tag cw-tag--best">Best match</span>}
          {p.helping && <span className="cw-tag cw-tag--help">Helping now</span>}
          {p.warning && <span className="cw-tag cw-tag--warn">{p.warning}</span>}
        </span>
        <span className="cw-row__meta">
          {level ? (
            <>
              <LevelBar level={level} />
              <span>{levelName(level)}</span>
            </>
          ) : (
            <span>{p.helping ? 'Already on this operation' : 'No skill level recorded'}</span>
          )}
          {p.free != null && <span className="cw-row__dot">{p.free}% of week free</span>}
        </span>
      </span>
      {p.match != null && (
        <span className="cw-row__score" title="Recommendation score (weighted sum)">
          <strong>{p.match}</strong>
          <span>match</span>
        </span>
      )}
      <CheckCircleFilled className="cw-row__check" aria-hidden />
    </button>
  );
}

/**
 * Admin: give a released, unfinished operation to another worker. Work not
 * started keeps its booked time; work that was started and is paused is
 * resumed by the new worker. Only people who are free, working and skilled
 * for that time are offered, best match first.
 */
export default function ChangeWorkerModal({
  op,
  onClose,
  onChanged,
}: {
  op: Operation | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [options, setOptions] = useState<OperationReplacements | null>(null);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<string | undefined>();
  const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!op) return;
    setOptions(null);
    setError('');
    setPicked(undefined);
    setQuery('');
    operationsApi
      .replacements(op.id)
      .then(({ data }) => {
        setOptions(data);
        setPicked(data.candidates[0]?.workerId ?? data.helpers[0]?.workerId);
      })
      .catch((err) => setError(getErrorMessage(err)));
  }, [op]);

  const started = op ? op.status !== 'SCHEDULED' && op.status !== 'PENDING' : false;

  const { helpers, free } = useMemo(() => {
    if (!options) return { helpers: [] as Person[], free: [] as Person[] };
    const firstClear = options.candidates.find((c) => !c.attendanceWarning)?.workerId;
    return {
      helpers: options.helpers.map<Person>((h) => ({
        workerId: h.workerId,
        fullName: h.fullName ?? 'Helper',
        nickname: h.nickname,
        photoVersion: h.photoVersion,
        helping: true,
      })),
      free: options.candidates.map<Person>((c) => ({
        workerId: c.workerId,
        fullName: c.fullName,
        nickname: c.nickname,
        photoVersion: c.photoVersion,
        level: c.proficiency,
        match: pct(c.score),
        free: pct(c.workload),
        warning: c.attendanceWarning,
        best: options.candidates.length > 1 && c.workerId === firstClear,
      })),
    };
  }, [options]);

  const q = query.trim().toLowerCase();
  const matches = (p: Person) =>
    !q || p.fullName.toLowerCase().includes(q) || (p.nickname || '').toLowerCase().includes(q);
  const shownHelpers = helpers.filter(matches);
  const shownFree = free.filter(matches);
  const total = helpers.length + free.length;
  const chosen = [...helpers, ...free].find((p) => p.workerId === picked);

  const save = async () => {
    if (!op || !picked) return;
    setSaving(true);
    try {
      const { data } = await operationsApi.handOver(op.id, picked);
      message.success(`${data.label} is now with ${data.to}`);
      onChanged();
      onClose();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const chosenName = chosen ? (chosen.nickname || chosen.fullName.split(' ')[0]) : '';

  return (
    <FormModal
      open={Boolean(op)}
      onCancel={onClose}
      icon={<UserSwitchOutlined />}
      title={started ? 'Hand over operation' : 'Change worker'}
      subtitle={op ? `${op.operationName || 'Operation'}${op.machineUnitLabel ? ` · ${op.machineUnitLabel}` : ''}` : undefined}
      size="md"
      onOk={save}
      okText={chosen ? `${started ? 'Hand over to' : 'Give to'} ${chosenName}` : started ? 'Hand over' : 'Change worker'}
      confirmLoading={saving}
      okButtonProps={{ disabled: !picked }}
      destroyOnHidden
    >
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {!error && !options ? (
        <div className="cw-loading">
          <Spin />
        </div>
      ) : null}
      {options && op ? (
        <div className="cw">
          <div className="cw-summary">
            <div className="cw-summary__cell">
              <span className="cw-summary__label">Now</span>
              <span className="cw-summary__who">
                <PersonAvatar
                  userId={op.assignedWorkerId}
                  fullName={op.assignedWorkerName}
                  photoVersion={op.assignedWorkerPhotoVersion}
                  size={24}
                />
                <span className="cw-row__text">
                  {op.assignedWorkerName ? personLabel(op.assignedWorkerName, op.assignedWorkerNickname) : 'Unassigned'}
                </span>
              </span>
            </div>
            <ArrowRightOutlined className="cw-summary__arrow" aria-hidden />
            <div className="cw-summary__cell">
              <span className="cw-summary__label">{started ? 'Resumes' : 'Booked'}</span>
              <span className="cw-summary__when">
                <CalendarOutlined />
                {bookedText(options.from, options.to)}
              </span>
            </div>
          </div>
          <p className="cw-note">
            {started
              ? 'The new worker resumes it; the time already worked is kept. '
              : 'The time stays the same. '}
            Only people who are free, on shift and skilled for it are listed, best match first.
          </p>

          {total === 0 ? (
            <Alert
              type="warning"
              showIcon
              message="Nobody qualified is free at that time."
              description="Use Reschedule to move the work to a time when someone is free."
            />
          ) : (
            <>
              {total > 6 && (
                <Input
                  allowClear
                  prefix={<SearchOutlined />}
                  placeholder="Search by name"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="cw-search"
                />
              )}
              <div className="cw-list" role="radiogroup" aria-label="Worker">
                {shownHelpers.length > 0 && (
                  <>
                    <div className="cw-group">Already on this operation</div>
                    {shownHelpers.map((p) => (
                      <PersonRow key={p.workerId} p={p} selected={p.workerId === picked} onPick={() => setPicked(p.workerId)} />
                    ))}
                  </>
                )}
                {shownFree.length > 0 && (
                  <>
                    <div className="cw-group">
                      Free at that time <span className="cw-group__count">{free.length}</span>
                    </div>
                    {shownFree.map((p) => (
                      <PersonRow key={p.workerId} p={p} selected={p.workerId === picked} onPick={() => setPicked(p.workerId)} />
                    ))}
                  </>
                )}
                {shownHelpers.length + shownFree.length === 0 && (
                  <div className="cw-empty">Nobody listed matches “{query}”.</div>
                )}
              </div>
            </>
          )}
        </div>
      ) : null}
    </FormModal>
  );
}
