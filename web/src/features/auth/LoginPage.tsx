import { Form, Input, Button, Alert } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { authApi, getOrCreateDeviceId } from '../../api/auth.api';
import { getErrorCode, getErrorMessage } from '../../api/client';
import { useOnline } from '../../offline/connectivity';
import { createActionQueue } from '../../offline/actionQueue';
import PinKeypad from './PinKeypad';
import './LoginPage.css';
import { OLD_PIN_HINT_KEY, PIN_HINT_KEY } from '../../auth/pinHint';

export default function LoginPage() {
  const { login, user, loading, applySession } = useAuth();
  const online = useOnline();
  const [form] = Form.useForm();
  const [error, setError] = useState('');
  const [pinError, setPinError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pinMode, setPinMode] = useState(false);
  const [checkingPin, setCheckingPin] = useState(true);
  const [failSignal, setFailSignal] = useState(0);
  const [params] = useSearchParams();
  const endReason = params.get('reason');
  const [waitingActions] = useState(() => createActionQueue(localStorage).list().length);

  useEffect(() => {
    getOrCreateDeviceId();
    let cancelled = false;
    (async () => {
      try {
        // A 6-digit PIN from before PINs became 4 digits no longer works.
        localStorage.removeItem(OLD_PIN_HINT_KEY);
        const hint = localStorage.getItem(PIN_HINT_KEY) === '1';
        if (!cancelled) setPinMode(hint);
      } finally {
        if (!cancelled) setCheckingPin(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!loading && user) {
    const dest = user.role === 'PRODUCTION_WORKER' ? '/home' : '/dashboard';
    return <Navigate to={dest} replace />;
  }

  const onPasswordLogin = async (values: { identifier: string; password: string }) => {
    setSubmitting(true);
    setError('');
    try {
      await login(values.identifier, values.password);
      sessionStorage.setItem('bmsc_offer_pin', '1');
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const onPinComplete = async (pin: string) => {
    setSubmitting(true);
    setPinError('');
    setError('');
    try {
      const { data } = await authApi.unlockWithPin(pin);
      applySession(data);
      localStorage.setItem(PIN_HINT_KEY, '1');
    } catch (err) {
      const code = getErrorCode(err);
      const message = getErrorMessage(err);
      if (code === 'PIN_LOCKED' || code === 'PIN_DEVICE_UNKNOWN') {
        localStorage.removeItem(PIN_HINT_KEY);
        setPinMode(false);
        setError(message);
        setPinError('');
        return;
      }
      setPinError(message);
      setFailSignal((n) => n + 1);
    } finally {
      setSubmitting(false);
    }
  };

  if (checkingPin) {
    return <div className="login-page" />;
  }

  return (
    <div className="login-page">
      <div className="login-page__bg" aria-hidden />
      <div className="login-page__veil" aria-hidden />

      <div className="login-card">
        <div className="login-card__header">
          <img className="login-card__logo" src="/logo.png" alt="Brothers Machine Shop logo" />
          <div className="login-card__brand">
            Brothers Machine Shop and
            <br />
            Services Corporation
          </div>
          <div className="login-card__subtitle">Production Scheduling System</div>
        </div>

        <div className="login-card__body">
          {!pinMode && (
            <>
              <h1 className="login-card__title">Sign in</h1>
              <p className="login-card__lead">Use your email or mobile number and password</p>
            </>
          )}

          {!online && (
            <Alert
              className="login-alert"
              type="warning"
              showIcon
              title="Needs a connection"
              description={
                waitingActions > 0
                  ? `${waitingActions} waiting action${waitingActions === 1 ? '' : 's'} will send after you sign in.`
                  : 'Sign-in needs the network.'
              }
            />
          )}
          {online &&
            (endReason === 'signed-out' || endReason === 'expired' || endReason === 'idle') &&
            !error && (
            <Alert
              className="login-alert"
              type={endReason === 'signed-out' ? 'warning' : 'info'}
              showIcon
              title={
                endReason === 'signed-out'
                  ? 'This device was signed out'
                  : endReason === 'idle'
                    ? 'Signed out after 30 minutes idle'
                    : 'Session expired — sign in again'
              }
              description={
                waitingActions > 0
                  ? `${waitingActions} waiting action${waitingActions === 1 ? '' : 's'} will send after you sign in.`
                  : undefined
              }
            />
          )}
          {error && <Alert className="login-alert" type="error" title={error} showIcon />}

          {pinMode ? (
            <PinKeypad
              title="Enter your PIN"
              error={pinError}
              busy={submitting}
              failSignal={failSignal}
              onComplete={onPinComplete}
              footer={
                <button
                  type="button"
                  className="pin-keypad__link"
                  onClick={() => {
                    setPinMode(false);
                    setPinError('');
                    setError('');
                  }}
                >
                  Use password instead
                </button>
              }
            />
          ) : (
            <Form scrollToFirstError={{ block: 'center' }}
              form={form}
              layout="vertical"
              onFinish={onPasswordLogin}
              autoComplete="off"
              requiredMark="optional"
            >
              <Form.Item
                name="identifier"
                label={
                  <span>
                    <span style={{ color: '#7A1528' }}>* </span>Email or mobile
                  </span>
                }
                rules={[{ required: true, message: 'Email or mobile is required' }]}
              >
                <Input
                  size="large"
                  prefix={<UserOutlined style={{ color: '#94a3b8' }} />}
                  autoComplete="username"
                  placeholder="name@example.com"
                />
              </Form.Item>
              <Form.Item
                name="password"
                label={
                  <span>
                    <span style={{ color: '#7A1528' }}>* </span>Password
                  </span>
                }
                rules={[{ required: true }]}
              >
                <Input.Password
                  size="large"
                  prefix={<LockOutlined style={{ color: '#94a3b8' }} />}
                  autoComplete="current-password"
                  placeholder="Enter your password"
                />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                block
                size="large"
                loading={submitting}
                className="login-card__submit"
              >
                Sign In
              </Button>
              <div style={{ textAlign: 'center', marginTop: 12 }}>
                <a href="/forgot-password" style={{ fontSize: 13 }}>
                  Forgot password?
                </a>
              </div>
              {localStorage.getItem(PIN_HINT_KEY) === '1' && (
                <Button type="link" block style={{ marginTop: 8 }} onClick={() => setPinMode(true)}>
                  Use PIN instead
                </Button>
              )}
            </Form>
          )}

          <div className="login-card__footer">
            <a href="/privacy">Privacy</a>
          </div>
        </div>
      </div>
    </div>
  );
}
