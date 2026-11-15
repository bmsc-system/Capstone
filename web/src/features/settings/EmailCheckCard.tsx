import { useCallback, useEffect, useState } from 'react';
import { Button, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getErrorMessage } from '../../api/client';
import { notificationsApi } from '../../api/notifications.api';
import StatusPill from '../../components/StatusPill';
import type { NotificationLog } from '../../types';
import { formatShop } from '../../utils/shopTime';

const KIND: Record<string, string> = {
  PASSWORD_RESET: 'Password reset',
  INVITATION: 'Account setup link',
};

const STATUS: Record<string, { label: string; color: 'green' | 'red' | 'amber' }> = {
  SENT: { label: 'Sent', color: 'green' },
  FAILED: { label: 'Failed', color: 'red' },
  SKIPPED: { label: 'Not sent', color: 'amber' },
};

/**
 * Admin: why an account email did not arrive. Lists the latest password-reset
 * and setup-link emails with whether each was sent and, when it was not, the
 * reason.
 */
export default function EmailCheckCard() {
  const [rows, setRows] = useState<NotificationLog[] | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await notificationsApi.list({ event: 'PASSWORD_RESET,INVITATION', limit: 15 });
      setRows(data);
    } catch (err) {
      setRows([]);
      message.error(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="sd-card" style={{ marginTop: 16, padding: 20 }}>
      <header className="sd-section__head">
        <h2 className="sd-section__title">Account emails</h2>
        <p className="sd-section__hint">
          Password resets and account setup links the system tried to send. When someone says an
          email did not arrive, the reason is here.
        </p>
      </header>

      <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '12px 0' }}>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} aria-label="Refresh the list" />
      </div>

      {rows === null ? (
        <div style={{ color: '#94a3b8' }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div style={{ color: '#94a3b8' }}>
          No password reset or setup link has been requested yet. A reset is only emailed to an
          active account that already has a password, using the exact email on the account.
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 8 }}>
          {rows.map((r) => {
            const st = STATUS[r.status] || STATUS.SKIPPED;
            return (
              <div
                key={r.id}
                style={{
                  border: '1px solid var(--c-border)',
                  borderRadius: 8,
                  padding: '8px 12px',
                  display: 'grid',
                  gap: 2,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <strong>{KIND[r.event || ''] || r.event}</strong>
                  <StatusPill color={st.color} compact>
                    {st.label}
                  </StatusPill>
                </div>
                <div style={{ fontSize: 12, color: '#64748b' }}>
                  To {r.recipient}
                  {r.createdAt ? ` · ${formatShop(r.createdAt, 'MMM D, h:mm A')}` : ''}
                </div>
                {r.status !== 'SENT' && r.errorMessage ? (
                  <div style={{ fontSize: 12, color: '#b45309' }}>{r.errorMessage}</div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
