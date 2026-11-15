import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Input, message } from 'antd';
import { MailOutlined, ReloadOutlined } from '@ant-design/icons';
import { getErrorMessage } from '../../api/client';
import { notificationsApi } from '../../api/notifications.api';
import StatusPill from '../../components/StatusPill';
import type { NotificationLog } from '../../types';
import { formatShop } from '../../utils/shopTime';

const KIND: Record<string, string> = {
  PASSWORD_RESET: 'Password reset',
  INVITATION: 'Account setup link',
  TEST: 'Test email',
};

const STATUS: Record<string, { label: string; color: 'green' | 'red' | 'amber' }> = {
  SENT: { label: 'Sent', color: 'green' },
  FAILED: { label: 'Failed', color: 'red' },
  SKIPPED: { label: 'Not sent', color: 'amber' },
};

/**
 * Admin: why an account email did not arrive. Lists the latest password-reset
 * and setup-link emails with whether each was sent and, when it was not, the
 * reason; and sends a test email through the same path.
 */
export default function EmailCheckCard() {
  const [rows, setRows] = useState<NotificationLog[] | null>(null);
  const [to, setTo] = useState('');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<NotificationLog | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await notificationsApi.list({ event: 'PASSWORD_RESET,INVITATION,TEST', limit: 15 });
      setRows(data);
    } catch (err) {
      setRows([]);
      message.error(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sendTest = async () => {
    if (!to.trim()) return;
    setSending(true);
    setResult(null);
    try {
      const { data } = await notificationsApi.sendTest('email', to.trim());
      setResult(data);
      await load();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="sd-card" style={{ marginTop: 16, padding: 20 }}>
      <header className="sd-section__head">
        <h2 className="sd-section__title">Account emails</h2>
        <p className="sd-section__hint">
          Password resets and account setup links the system tried to send. When someone says an
          email did not arrive, the reason is here.
        </p>
      </header>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '12px 0' }}>
        <Input
          style={{ maxWidth: 320 }}
          prefix={<MailOutlined />}
          placeholder="Send a test email to…"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          onPressEnter={sendTest}
        />
        <Button type="primary" loading={sending} disabled={!to.trim()} onClick={sendTest}>
          Send test email
        </Button>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} aria-label="Refresh the list" />
      </div>
      {result ? (
        <Alert
          style={{ marginBottom: 12 }}
          showIcon
          type={result.status === 'SENT' ? 'success' : 'warning'}
          message={
            result.status === 'SENT'
              ? `Sent to ${result.recipient}. If it is not in the inbox in a minute, check Spam.`
              : `Not sent: ${result.errorMessage || 'no reason given'}`
          }
        />
      ) : null}

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
