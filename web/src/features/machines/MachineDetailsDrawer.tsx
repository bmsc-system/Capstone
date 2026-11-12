import { useEffect, useState, type ReactNode } from 'react';
import { Button, Drawer, Skeleton } from 'antd';
import { CloseOutlined, TeamOutlined } from '@ant-design/icons';
import { operationsApi } from '../../api/operations.api';
import { CrewChips, PersonChip } from '../../components/PersonAvatar';
import { IdChip, StatusPill } from '../../components/ui';
import { useViewport } from '../../hooks/useViewport';
import { MACHINE_STATUS } from '../../theme/statuses';
import type { AffectedScheduledOperation, MachineDowntimeRecord, MachineUnitStatus } from '../../types';
import { formatShop } from '../../utils/shopTime';
import { accentVars, machineArt, type MachineKind } from './machineKinds';
import {
  cardStatus,
  formatOpenDuration,
  whenLabel,
  windowLabel,
  type MachineActions,
} from './machineStatus';

function Section({ title, children, tone }: { title: string; children: ReactNode; tone?: 'down' }) {
  return (
    <section className={`mdrawer__section${tone ? ` mdrawer__section--${tone}` : ''}`}>
      <h4 className="mdrawer__section-title">{title}</h4>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mdrawer__row">
      <span className="mdrawer__label">{label}</span>
      <span className="mdrawer__value">{children}</span>
    </div>
  );
}

function OperationBlock({
  op,
  onOpenJob,
  showFinish,
}: {
  op: AffectedScheduledOperation;
  onOpenJob: (jobOrderId: string) => void;
  showFinish?: boolean;
}) {
  return (
    <>
      <div className="mdrawer__op">{op.operationName}</div>
      <div className="mdrawer__op-job">
        {op.jobNumber && <IdChip to={`/job-orders/${op.jobOrderId}`}>{op.jobNumber}</IdChip>}
        {op.jobTitle && <span className="mdrawer__muted">{op.jobTitle}</span>}
      </div>
      <Row label="Crew">
        <CrewChips
          crew={op.crew}
          lead={{
            fullName: op.assignedWorkerName,
            nickname: op.assignedWorkerNickname,
            photoVersion: op.assignedWorkerPhotoVersion,
          }}
          vertical
        />
      </Row>
      <Row label="Scheduled">{windowLabel(op.scheduledStart, op.scheduledEnd)}</Row>
      {showFinish && op.scheduledEnd && <Row label="Expected finish">{whenLabel(op.scheduledEnd)}</Row>}
      <Button size="small" onClick={() => onOpenJob(op.jobOrderId)} className="mdrawer__link-btn">
        Open job order
      </Button>
    </>
  );
}

function downtimeLength(row: MachineDowntimeRecord): string {
  if (!row.endedAt) return 'Still open';
  return formatOpenDuration(row.startedAt, new Date(row.endedAt).getTime());
}

export default function MachineDetailsDrawer({
  unit,
  kind,
  typeName,
  nowMs,
  actions,
  onClose,
  canEdit,
}: {
  unit: MachineUnitStatus | null;
  kind: MachineKind;
  typeName: string;
  nowMs: number;
  actions: MachineActions;
  onClose: () => void;
  /** Admin only: machine setup, breakdowns, and repairs. Office Staff view. */
  canEdit: boolean;
}) {
  const { isPhone } = useViewport();
  const [history, setHistory] = useState<MachineDowntimeRecord[] | null>(null);
  const unitId = unit?.id;
  const openDowntimeId = unit?.openDowntime?.id;

  useEffect(() => {
    if (!unitId) return;
    let live = true;
    setHistory(null);
    operationsApi
      .machineUnitDowntime(unitId)
      .then(({ data }) => live && setHistory(data))
      .catch(() => live && setHistory([]));
    return () => {
      live = false;
    };
  }, [unitId, openDowntimeId]);

  const status = unit ? cardStatus(unit) : 'idle';
  const down = unit?.openDowntime;
  const past = (history || []).filter((r) => r.id !== down?.id);

  return (
    <Drawer
      open={!!unit}
      onClose={onClose}
      size={isPhone ? '100%' : 440}
      closable={false}
      title={null}
      destroyOnHidden
      rootClassName="mdrawer-root"
      styles={{ body: { padding: 0 } }}
    >
      {unit && (
        <div className="mdrawer" style={accentVars(kind)}>
          <header className="mdrawer__head">
            <div className="mdrawer__art">
              <img src={machineArt(kind)} alt="" />
            </div>
            <div className="mdrawer__titles">
              <h3 className="mdrawer__name">{unit.label}</h3>
              <div className="mdrawer__type">{typeName}</div>
              <StatusPill status={status} map={MACHINE_STATUS} compact dot />
            </div>
            <button type="button" className="mdrawer__close" onClick={onClose} aria-label="Close details">
              <CloseOutlined />
            </button>
          </header>

          <div className="mdrawer__body">
            {status === 'breakdown' && down && (
              <Section title="Breakdown" tone="down">
                <div className="mdrawer__reason">{down.reason}</div>
                <div className="mdrawer__down-for">Down {formatOpenDuration(down.startedAt, nowMs)}</div>
                {down.note && <p className="mdrawer__note">{down.note}</p>}
                <Row label="Reported by">{down.reportedByName || '—'}</Row>
                <Row label="Since">{formatShop(down.startedAt, 'ddd D MMM, h:mm A')}</Row>
                <Row label="Expected repair">
                  {down.expectedRepairDate ? formatShop(down.expectedRepairDate, 'ddd, MMM D, YYYY') : 'Not set'}
                </Row>
                {unit.affectedCount > 0 && (
                  <Row label="To reschedule">
                    {unit.affectedCount} operation{unit.affectedCount === 1 ? '' : 's'}
                  </Row>
                )}
                <div className="mdrawer__actions">
                  {canEdit && (
                    <>
                      <Button onClick={() => actions.onSetRepairDate(unit)}>
                        {down.expectedRepairDate ? 'Change repair date' : 'Set repair date'}
                      </Button>
                      <Button type="primary" onClick={() => actions.onClose(unit)}>
                        Close breakdown
                      </Button>
                    </>
                  )}
                  {canEdit && unit.affectedCount > 0 && actions.onBreakdownWork ? (
                    <Button onClick={() => actions.onBreakdownWork?.(unit)}>Move booked work</Button>
                  ) : null}
                </div>
              </Section>
            )}

            <Section title="Assigned operator">
              <div className="mdrawer__assigned">
                {unit.defaultOperatorId ? (
                  <PersonChip
                    userId={unit.defaultOperatorId}
                    fullName={unit.defaultOperatorName}
                    nickname={unit.defaultOperatorNickname}
                    photoVersion={unit.defaultOperatorPhotoVersion}
                    size={32}
                    strong
                  />
                ) : (
                  <span className="mdrawer__open">
                    <TeamOutlined /> Open to all
                  </span>
                )}
              </div>
              {canEdit && status !== 'retired' && (
                <div className="mdrawer__actions">
                  <Button onClick={() => actions.onSetDefaultOperator(unit)}>
                    {unit.defaultOperatorId ? 'Change' : 'Assign operator'}
                  </Button>
                  {unit.defaultOperatorId && (
                    <Button onClick={() => actions.onClearDefaultOperator(unit)}>Open to all</Button>
                  )}
                </div>
              )}
            </Section>

            {unit.currentOperation && status !== 'breakdown' && (
              <Section title="Running now">
                <OperationBlock op={unit.currentOperation} onOpenJob={actions.onOpenJob} showFinish />
              </Section>
            )}

            <Section title="Next job">
              {unit.nextOperation ? (
                <OperationBlock op={unit.nextOperation} onOpenJob={actions.onOpenJob} />
              ) : (
                <div className="mdrawer__muted">Nothing scheduled on this unit.</div>
              )}
            </Section>

            <Section title="Today">
              <Row label="Jobs today">{unit.jobsToday ?? 0}</Row>
              <Row label="Open operations">{unit.affectedCount}</Row>
            </Section>

            <Section title="Breakdown history">
              {history === null ? (
                <Skeleton active paragraph={{ rows: 2 }} title={false} />
              ) : past.length === 0 ? (
                <div className="mdrawer__muted">No earlier breakdowns recorded.</div>
              ) : (
                <ul className="mdrawer__history">
                  {past.slice(0, 8).map((row) => (
                    <li key={row.id}>
                      <div className="mdrawer__history-main">
                        <span className="mdrawer__history-reason">{row.reason}</span>
                        <span className="mdrawer__history-len ui-tabular">{downtimeLength(row)}</span>
                      </div>
                      <div className="mdrawer__muted">
                        {formatShop(row.startedAt, 'MMM D, YYYY')}
                        {row.reportedByName ? ` · reported by ${row.reportedByName}` : ''}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            {canEdit && (
            <div className="mdrawer__footer">
              {status === 'retired' ? (
                <Button type="primary" onClick={() => actions.onRestore(unit)}>
                  Restore machine
                </Button>
              ) : (
                <>
                  {!unit.down && (
                    <Button danger onClick={() => actions.onReport(unit)}>
                      Mark as down
                    </Button>
                  )}
                  <Button type="text" danger onClick={() => actions.onRetire(unit)}>
                    Remove machine
                  </Button>
                </>
              )}
            </div>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}
