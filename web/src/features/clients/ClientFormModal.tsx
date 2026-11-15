import { useEffect, useState } from 'react';
import { Button, Col, Form, Input, Modal, Row, Switch, message } from 'antd';
import { ContactsOutlined } from '@ant-design/icons';
import { clientsApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import type { Client } from '../../types';
import './clientForm.css';

function sectionLabel(text: string) {
  return <div className="app-form-section">{text}</div>;
}

/** Register a client, or edit one when `client` is set. */
export default function ClientFormModal({
  open,
  client,
  onClose,
  onSaved,
}: {
  open: boolean;
  client: Client | null;
  onClose: () => void;
  onSaved: (saved: Client) => void;
}) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const notifyByEmail = Form.useWatch('notifyByEmail', form);
  const email = Form.useWatch('email', form);

  useEffect(() => {
    if (!open) return;
    if (client) {
      form.setFieldsValue({
        name: client.name,
        contact: client.contact,
        email: client.email,
        mobileNumber: client.mobileNumber,
        notifyByEmail: !!client.notifyByEmail,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ notifyByEmail: false });
    }
  }, [open, client, form]);

  const onSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload = {
        name: values.name,
        contact: values.contact || undefined,
        email: values.email || undefined,
        mobileNumber: values.mobileNumber || undefined,
        notifyByEmail: !!values.notifyByEmail,
        // One kind of job update: detailed progress.
        updateLevel: 'DETAILED' as const,
      };
      if (client) {
        const { data } = await clientsApi.update(client.id, payload);
        message.success('Client updated');
        onSaved(data);
      } else {
        const { data } = await clientsApi.create(payload);
        message.success('Client registered');
        onSaved(data);
      }
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const emailMissing = !!notifyByEmail && !(email || '').trim();

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
      centered
      forceRender
      className="app-form-modal"
      styles={{
        container: { padding: 0, borderRadius: 0, overflow: 'hidden' },
        body: { padding: 0 },
      }}
      closable={false}
    >
      <div className="app-form-modal__head">
        <div className="app-form-modal__icon">
          <ContactsOutlined />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="app-form-modal__title">{client ? 'Edit client' : 'Create client'}</div>
        </div>
        <button type="button" className="app-form-modal__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <Form form={form} layout="vertical" style={{ padding: '20px 24px 8px' }}>
        {sectionLabel('Client')}
        <Form.Item
          name="name"
          label="Name"
          rules={[{ required: true, message: 'Name is required' }]}
          style={{ marginBottom: 14 }}
        >
          <Input placeholder="Company or person name" />
        </Form.Item>
        <Form.Item name="contact" label="Contact person / notes" style={{ marginBottom: 18 }}>
          <Input placeholder="Optional" />
        </Form.Item>

        {sectionLabel('Contact')}
        <Row gutter={12}>
          <Col xs={24} sm={14}>
            <Form.Item
              name="email"
              label="Email"
              rules={[{ type: 'email', message: 'Enter a valid email' }]}
              style={{ marginBottom: 14 }}
            >
              <Input placeholder="client@example.com" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={10}>
            <Form.Item name="mobileNumber" label="Mobile" style={{ marginBottom: 14 }}>
              <Input placeholder="+63917…" />
            </Form.Item>
          </Col>
        </Row>

        <div className="client-notify">
          <div className="client-notify__row">
            <div className="client-notify__copy">
              <div className="client-notify__title">Send job updates by email</div>
            </div>
            <Form.Item name="notifyByEmail" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch />
            </Form.Item>
          </div>
          {emailMissing && (
            <div style={{ fontSize: 12, color: '#b45309', marginTop: 8 }}>
              Add an email address to send updates.
            </div>
          )}
        </div>
      </Form>

      <div className="app-form-modal__footer">
        <Button onClick={onClose} style={{ minWidth: 96 }}>
          Cancel
        </Button>
        <Button type="primary" loading={saving} onClick={onSave} style={{ fontWeight: 700, minWidth: 120 }}>
          {client ? 'Save' : 'Register'}
        </Button>
      </div>
    </Modal>
  );
}
