import { Alert, Button, Form, Input } from 'antd';
import { ArrowLeftOutlined, ArrowRightOutlined, MailOutlined, UserOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { authApi } from '../../api/auth.api';
import { getErrorMessage } from '../../api/client';
import AuthShell, { AuthFooter } from './AuthShell';
import './LoginPage.css';

export default function ForgotPasswordPage() {
  const [form] = Form.useForm();
  const [error, setError] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: { identifier: string }) => {
    setSubmitting(true);
    setError('');
    try {
      await authApi.requestPasswordReset(values.identifier);
      setSubmitted(true);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const back = (
    <Link className="login-card__back" to="/login">
      <ArrowLeftOutlined /> Back to sign in
    </Link>
  );

  return (
    <AuthShell>
      {submitted ? (
        <>
          <div className="login-card__icon" aria-hidden>
            <MailOutlined />
          </div>
          <h2 className="login-card__title">Check your email</h2>
          <p className="login-card__lead">If the account has an email, a reset link is on its way.</p>
        </>
      ) : (
        <>
          <div className="login-card__eyebrow">Account help</div>
          <h2 className="login-card__title">Forgot password</h2>
          <p className="login-card__lead">We&apos;ll email you a reset link.</p>
        </>
      )}

      {error && <Alert className="login-alert" type="error" title={error} showIcon />}

      {!submitted && (
        <Form
          scrollToFirstError={{ block: 'center' }}
          form={form}
          layout="vertical"
          onFinish={onFinish}
          autoComplete="off"
          requiredMark={false}
        >
          <Form.Item
            name="identifier"
            label="Email or mobile number"
            rules={[{ required: true, message: 'Enter your email or mobile number' }]}
          >
            <Input
              size="large"
              autoFocus
              prefix={<UserOutlined style={{ color: '#5b6475' }} />}
              placeholder="name@example.com"
            />
          </Form.Item>
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
            Send reset link
          </Button>
        </Form>
      )}

      <AuthFooter>{back}</AuthFooter>
    </AuthShell>
  );
}
