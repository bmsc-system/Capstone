import { useState } from 'react';
import { Button, Modal } from 'antd';
import {
  CloudUploadOutlined,
  DisconnectOutlined,
  ExclamationCircleFilled,
  SyncOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import { formatShop } from '../utils/shopTime';
import { useOfflineOptional, type OfflineContextValue } from './OfflineProvider';
import { confirmAction } from '../utils/confirmAction';

/**
 * Connection and waiting actions as a small pill in the worker top bar.
 * Hidden when online with nothing waiting; tap for details (and to retry or
 * discard an action the server did not accept).
 */
export default function OfflineStatusBar() {
  const offline = useOfflineOptional();
  return offline ? <SyncPill {...offline} /> : null;
}

function SyncPill({
  online,
  lastUpdated,
  pending,
  refused,
  otherUsersPending,
  syncing,
  retry,
  discard,
}: OfflineContextValue) {
  const [open, setOpen] = useState(false);
  const waiting = pending.filter((a) => a.state === 'waiting').length + (refused ? 1 : 0);
  if (online && !waiting && !refused && !otherUsersPending) return null;

  const confirmDiscard = () => {
    if (!refused) return;
    confirmAction({
      icon: <DeleteOutlined />,
      title: 'Discard this action?',
      content: `"${refused.label}" at ${formatShop(refused.at, 'MMM D, h:mm A')} will not be recorded.`,
      okText: 'Discard',
      danger: true,
      cancelText: 'Keep it',
      onOk: () => {
        discard(refused.id);
        setOpen(false);
      },
    });
  };

  let variant = '';
  let icon = <CloudUploadOutlined />;
  let text = `${waiting} waiting`;
  if (refused) {
    variant = ' w-sync--refused';
    icon = <ExclamationCircleFilled />;
    text = 'Not accepted';
  } else if (!online) {
    variant = ' w-sync--offline';
    icon = <DisconnectOutlined />;
    text = waiting ? `Offline · ${waiting}` : 'Offline';
  } else if (syncing && waiting) {
    icon = <SyncOutlined className="w-sync__spin" />;
    text = `Sending ${waiting}`;
  } else if (!waiting) {
    text = 'Other user';
  }

  const label = [
    online ? null : 'Offline',
    waiting ? `${waiting} ${waiting === 1 ? 'action' : 'actions'} waiting to send` : null,
    refused ? 'one was not accepted' : null,
  ]
    .filter(Boolean)
    .join(', ');

  return (
    <>
      <button
        type="button"
        className={`w-sync${variant}`}
        onClick={() => setOpen(true)}
        aria-label={label || 'Connection status'}
      >
        {icon}
        <span>{text}</span>
      </button>

      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        title={refused ? 'This action was not accepted' : online ? 'Waiting to send' : 'You are offline'}
        footer={null}
        destroyOnHidden
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {!online && (
            <div style={{ fontSize: 14, color: '#334155' }}>
              {lastUpdated
                ? `Your jobs were last updated ${formatShop(lastUpdated, 'MMM D, h:mm A')}. `
                : 'Nothing is saved on this phone yet. '}
              Start, pause, resume and complete still work: they are saved on this phone and sent when
              the connection returns.
            </div>
          )}
          {waiting > 0 && !refused && (
            <div style={{ fontSize: 14, color: '#334155' }}>
              {waiting === 1 ? '1 action is' : `${waiting} actions are`} waiting to send
              {online && syncing ? ' and sending now.' : '.'} They are sent in the order you did them.
            </div>
          )}
          {refused && (
            <>
              <div>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{refused.label}</div>
                <div style={{ color: '#64748b', fontSize: 13 }}>
                  Recorded on this phone {formatShop(refused.at, 'MMM D, h:mm A')}
                </div>
              </div>
              <div
                style={{
                  background: '#fef2f2',
                  border: '1px solid #fecaca',
                  borderRadius: 10,
                  padding: '10px 12px',
                  color: '#7A1528',
                  fontSize: 14,
                }}
              >
                {refused.error?.message || 'The server refused this action.'}
              </div>
              <div style={{ fontSize: 13, color: '#64748b' }}>
                Actions after this one wait until you try again or discard it, so nothing is recorded
                out of order.
                {waiting > 1 ? ` ${waiting - 1} more waiting behind it.` : ''}
              </div>
              <Button
                type="primary"
                block
                size="large"
                disabled={!online}
                onClick={() => {
                  retry(refused.id);
                  setOpen(false);
                }}
              >
                {online ? 'Try again' : 'Try again (needs a connection)'}
              </Button>
              <Button danger block size="large" onClick={confirmDiscard}>
                Discard
              </Button>
            </>
          )}
          {otherUsersPending > 0 && (
            <div style={{ fontSize: 13, color: '#64748b' }}>
              {otherUsersPending === 1 ? '1 action' : `${otherUsersPending} actions`} from another worker
              on this phone will be sent when they sign in.
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
