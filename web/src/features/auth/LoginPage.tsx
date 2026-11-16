import { Form, Input, Button, Alert } from 'antd';
import { ArrowRightOutlined, UserOutlined, LockOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { authApi, getOrCreateDeviceId } from '../../api/auth.api';
import { getErrorCode, getErrorMessage } from '../../api/client';
import { useOnline } from '../../offline/connectivity';
import { createActionQueue } from '../../offline/actionQueue';
import PinKeypad from './PinKeypad';
import AuthShell, { AuthFooter } from './AuthShell';
import './LoginPage.css';
import { OLD_PIN_HINT_KEY, PIN_HINT_KEY, phoneHasPinHint } from '../../auth/pinHint';
import { clearPinCheck, savePinCheck } from '../../auth/appLock';

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
        // Desktops always use the password; only phones open on the keypad.
        const hint = phoneHasPinHint();
        // "Use password instead" on the app lock opens the password form.
        if (!cancelled) setPinMode(hint && new URLSearchParams(window.location.search).get('mode') !== 'password');
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
      void savePinCheck(pin);
    } catch (err) {
      const code = getErrorCode(err);
      const message = getErrorMessage(err);
      if (code === 'PIN_LOCKED' || code === 'PIN_DEVICE_UNKNOWN') {
        localStorage.removeItem(PIN_HINT_KEY);
        clearPinCheck();
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
    return <div className="auth" />;
  }

  return (
    <AuthShell>
      <div className="login-card__eyebrow">Welcome back</div>
      {!pinMode && (
        <>
          <h2 className="login-card__title">Sign in</h2>
          <p className="login-card__lead">Use your email or mobile number and password.</p>
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
        (endReason === 'signed-out' || endReason === 'expired' || endReason === 'idle' || endReason === 'pin-locked') &&
        !error && (
        <Alert
          className="login-alert"
          type={endReason === 'signed-out' || endReason === 'pin-locked' ? 'warning' : 'info'}
          showIcon
          title={
            endReason === 'signed-out'
              ? 'This device was signed out'
              : endReason === 'pin-locked'
                ? 'Too many wrong PINs. Sign in with your password.'
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
        <Form
          scrollToFirstError={{ block: 'center' }}
          form={form}
          layout="vertical"
          onFinish={onPasswordLogin}
          autoComplete="off"
          requiredMark={false}
        >
          <Form.Item
            name="identifier"
            label="Email or mobile"
            rules={[{ required: true, message: 'Email or mobile is required' }]}
          >
            <Input
              size="large"
              prefix={<UserOutlined style={{ color: '#5b6475' }} />}
              autoComplete="username"
              placeholder="name@example.com"
            />
          </Form.Item>
          <Form.Item name="password" label="Password" rules={[{ required: true, message: 'Password is required' }]}>
            <Input.Password
              size="large"
              prefix={<LockOutlined style={{ color: '#5b6475' }} />}
              autoComplete="current-password"
              placeholder="Enter your password"
            />
          </Form.Item>
          <div className="login-card__row">
            <a className="login-card__link" href="/forgot-password">
              Forgot password?
            </a>
          </div>
          <Button
            type="primary"
            htmlType="submit"
            block
            size="large"
            loading={submitting}
            className="login-card__submit"
            icon={<ArrowRightOutlined />}
            iconPlacement="end"
          >
            Sign in
          </Button>
          {phoneHasPinHint() && (
            <Button type="link" block style={{ marginTop: 8 }} onClick={() => setPinMode(true)}>
              Use PIN instead
            </Button>
          )}
        </Form>
      )}

      <AuthFooter />
    </AuthShell>
  );
}
