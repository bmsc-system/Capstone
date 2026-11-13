import { useEffect, useMemo, useState } from 'react';
import {
  Button, Form, Input, Select, Dropdown, message, Tooltip, Space, Row, Col,
} from 'antd';
import type { MenuProps, TableColumnsType } from 'antd';
import {
  PlusOutlined,
  UserAddOutlined,
  SearchOutlined,
  MoreOutlined,
  StopOutlined,
  CheckCircleOutlined,
  EditOutlined,
  CopyOutlined,
} from '@ant-design/icons';
import { usersApi, type InviteDelivery } from '../../api/users.api';
import { getErrorMessage } from '../../api/client';
import StatusPill, { type PillColor } from '../../components/StatusPill';
import SelectMultipleIcon from '../../components/SelectMultipleIcon';
import { EllipsisText, ResponsiveTable, rowOpenProps, FormModal } from '../../components/ui';
import UserIdentity from '../../components/UserIdentity';
import type { User, UserRole } from '../../types';
import { confirmAction, showNotice } from '../../utils/confirmAction';

const roleStyle: Record<string, { label: string; color: PillColor }> = {
  ADMIN: { label: 'Administrator', color: 'blue' },
  OFFICE_STAFF: { label: 'Office Staff', color: 'amber' },
  PRODUCTION_WORKER: { label: 'Production Worker', color: 'teal' },
};

type StatusFilter = 'active' | 'inactive';

function SetupLinkBlock({ setupUrl }: { setupUrl: string }) {
  return (
    <Space.Compact style={{ width: '100%', marginTop: 12 }}>
      <Input value={setupUrl} readOnly />
      <Button
        icon={<CopyOutlined />}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(setupUrl);
            message.success('Link copied');
          } catch {
            message.error('Could not copy');
          }
        }}
      >
        Copy
      </Button>
    </Space.Compact>
  );
}

/** The copy-link fallback, for when the setup email does not arrive. */
function showSetupLink(setupUrl: string | null | undefined, name: string) {
  if (!setupUrl) return;
  showNotice({
    title: 'Setup link',
    wide: true,
    okText: 'Done',
    content: (
      <>
        Send this link to {name} yourself, for example by chat. It works once, for 48 hours,
        and any link sent before it no longer works.
        <SetupLinkBlock setupUrl={setupUrl} />
      </>
    ),
  });
}

/** A failed send stays on screen until dismissed, with the link to copy, so the
 *  admin knows the person has no way in yet. */
function reportInviteDelivery(
  delivery: InviteDelivery | undefined,
  saved: string,
  setupUrl?: string | null,
) {
  if (delivery?.status === 'SENT') {
    message.success(`${saved ? `${saved} ` : ''}Setup link sent${delivery.recipient ? ` to ${delivery.recipient}` : ''}.`);
    return;
  }
  showNotice({
    title: 'Setup link not sent',
    wide: true,
    okText: 'Done',
    content: (
      <>
        {delivery?.errorMessage ? `${delivery.errorMessage}. ` : ''}Copy the link and send it
        yourself, or try sending it again from the row menu.
        {setupUrl ? <SetupLinkBlock setupUrl={setupUrl} /> : null}
      </>
    ),
  });
}

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<UserRole[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter[]>([]);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [form] = Form.useForm();
  const [editUser, setEditUser] = useState<User | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editForm] = Form.useForm();
  const editRole = Form.useWatch('role', editForm) as UserRole | undefined;
  const fetchUsers = async () => {
    setLoading(true);
    try {
      const { data } = await usersApi.list();
      setUsers(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return users.filter((u) => {
      if (q && !`${u.fullName} ${u.nickname || ''} ${u.email || ''}`.toLowerCase().includes(q))
        return false;
      if (roleFilter.length && !roleFilter.includes(u.role)) return false;
      if (statusFilter.length) {
        const status: StatusFilter = u.active ? 'active' : 'inactive';
        if (!statusFilter.includes(status)) return false;
      }
      return true;
    });
  }, [users, query, roleFilter, statusFilter]);

  const closeModal = () => {
    setModalOpen(false);
    form.resetFields();
  };

  const onCreate = async (values: {
    email: string;
    fullName: string;
    nickname?: string;
    role: string;
    mobileNumber?: string;
  }) => {
    setSubmitting(true);
    try {
      const { data } = await usersApi.create({
        email: values.email.trim(),
        fullName: values.fullName,
        nickname: values.nickname?.trim() || undefined,
        role: values.role,
        mobileNumber: values.mobileNumber?.trim() || undefined,
      });
      reportInviteDelivery(data.delivery, 'User saved.', data.setupUrl);
      closeModal();
      fetchUsers();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (u: User) => {
    setEditUser(u);
    editForm.setFieldsValue({
      fullName: u.fullName,
      nickname: u.nickname || '',
      email: u.email || '',
      mobileNumber: u.mobileNumber || '',
      role: u.role,
    });
  };

  const sendInvite = async (u: User) => {
    try {
      const { data } = await usersApi.resendInvite(u.id);
      reportInviteDelivery(data.delivery, '', data.setupUrl);
      fetchUsers();
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const copyInviteLink = (u: User) => {
    confirmAction({
      title: `Make a new setup link for ${u.fullName}?`,
      content: 'Any setup link sent before stops working. Nothing is emailed.',
      okText: 'Make link',
      onOk: async () => {
        try {
          const { data } = await usersApi.resendInvite(u.id, { deliver: false });
          showSetupLink(data.setupUrl, u.fullName);
          fetchUsers();
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });
  };

  const onEditSave = async (values: {
    fullName: string;
    nickname?: string;
    email?: string;
    mobileNumber?: string;
    role: UserRole;
  }) => {
    if (!editUser) return;
    setEditSaving(true);
    try {
      const { data } = await usersApi.update(editUser.id, {
        fullName: values.fullName.trim(),
        nickname: (values.nickname || '').trim() || null,
        email: (values.email || '').trim(),
        mobileNumber: (values.mobileNumber || '').trim() || undefined,
        role: values.role,
      });
      if (data.delivery) {
        // A pending user's email changed: the old link stopped working and a new one went out.
        reportInviteDelivery(data.delivery, 'Details saved. The old setup link no longer works.', data.setupUrl);
      } else {
        message.success(
          values.role !== editUser.role
            ? 'Saved. The new role applies at their next sign-in.'
            : 'Details saved'
        );
      }
      setEditUser(null);
      fetchUsers();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setEditSaving(false);
    }
  };

  const onDeactivate = async (id: string) => {
    try {
      await usersApi.deactivate(id);
      message.success('User disabled');
      fetchUsers();
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const onReenable = async (id: string) => {
    try {
      await usersApi.update(id, { active: true });
      message.success('User re-enabled');
      fetchUsers();
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const confirmDisable = (id: string, name: string) => {
    confirmAction({
      title: `Disable ${name}?`,
      content: 'They can no longer sign in. Their history is kept.',
      okText: 'Disable',
      danger: true,
      onOk: () => onDeactivate(id),
    });
  };

  const columns: TableColumnsType<User> = [
    {
      title: 'Name',
      dataIndex: 'fullName',
      key: 'fullName',
      sorter: (a, b) => a.fullName.localeCompare(b.fullName),
      render: (_: string, record: User) => (
        <UserIdentity
          userId={record.id}
          fullName={record.fullName}
          nickname={record.nickname}
          email={record.email}
          mobileNumber={record.mobileNumber}
          size={32}
          phoneEmptyContact="No email yet"
        />
      ),
    },
    {
      title: 'Email',
      dataIndex: 'email',
      key: 'email',
      ellipsis: true,
      sorter: (a, b) => (a.email || '').localeCompare(b.email || ''),
      render: (v: string | null) =>
        v ? (
          <EllipsisText>{v}</EllipsisText>
        ) : (
          <span style={{ fontSize: 13, color: '#94a3b8' }}>No email yet</span>
        ),
    },
    {
      title: 'Role',
      dataIndex: 'role',
      key: 'role',
      width: 170,
      render: (r: string) => {
        const st = roleStyle[r] || { label: r.replace('_', ' '), color: 'gray' as PillColor };
        return <StatusPill color={st.color} plain>{st.label}</StatusPill>;
      },
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (_: unknown, record: User) => {
        const status = record.status || (record.active ? 'ACTIVE' : 'DISABLED');
        const color =
          status === 'ACTIVE' ? 'green' : status === 'INVITED' ? 'amber' : 'red';
        const label =
          status === 'ACTIVE' ? 'Active' : status === 'INVITED' ? 'Invited' : 'Disabled';
        return (
          <StatusPill color={color} compact>
            {label}
          </StatusPill>
        );
      },
    },
    {
      title: '',
      key: 'actions',
      width: 56,
      align: 'center',
      render: (_: unknown, record: User) => {
        const items: MenuProps['items'] = [
          {
            key: 'edit',
            icon: <EditOutlined />,
            label: 'Edit details',
            onClick: () => openEdit(record),
          },
        ];
        const status = record.status || (record.active ? 'ACTIVE' : 'DISABLED');
        if (status === 'INVITED') {
          items.push({
            key: 'invite-send',
            label: record.email ? 'Send setup link' : 'Send setup link (add an email first)',
            disabled: !record.email,
            onClick: () => sendInvite(record),
          });
          items.push({
            key: 'invite-copy',
            label: 'Copy setup link',
            disabled: !record.email,
            onClick: () => copyInviteLink(record),
          });
          items.push({
            key: 'revoke-invite',
            label: 'Revoke invite',
            onClick: async () => {
              try {
                await usersApi.revokeInvite(record.id);
                message.success('Invitation revoked');
                fetchUsers();
              } catch (err) {
                message.error(getErrorMessage(err));
              }
            },
          });
        }
        if (status !== 'DISABLED') {
          items.push({
            key: 'deactivate',
            icon: <StopOutlined />,
            danger: true,
            label: 'Disable',
            onClick: () => confirmDisable(record.id, record.fullName),
          });
        } else {
          items.push({
            key: 'reenable',
            icon: <CheckCircleOutlined />,
            label: 'Re-enable',
            onClick: () => {
              confirmAction({
                title: `Re-enable ${record.fullName}?`,
                content: 'They can sign in again with their current password.',
                okText: 'Re-enable',
                onOk: () => onReenable(record.id),
              });
            },
          });
        }
        if (!items.length) return null;
        return (
          <Dropdown menu={{ items }} trigger={['click']} placement="bottomRight">
            <Button
              type="text"
              size="small"
              icon={<MoreOutlined style={{ fontSize: 18 }} />}
              aria-label="More actions"
            />
          </Dropdown>
        );
      },
    },
  ];


  const sectionLabel = (text: string) => (
    <div
      style={{
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: 0.8,
        textTransform: 'uppercase',
        color: '#64748b',
        marginBottom: 12,
        paddingBottom: 8,
        borderBottom: '1px solid #e2e8f0',
      }}
    >
      {text}
    </div>
  );

  return (
    <div className="std-list-page">
      <div className="std-list-toolbar">
        <div className="std-list-filters">
          <Input
            allowClear
            placeholder="Search name or email…"
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="std-list-search"
          />
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            placeholder="Role"
            className="std-list-filter"
            value={roleFilter}
            onChange={setRoleFilter}
            options={[
              { value: 'ADMIN', label: 'Administrator' },
              { value: 'OFFICE_STAFF', label: 'Office Staff' },
              { value: 'PRODUCTION_WORKER', label: 'Production Worker' },
            ]}
          />
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            placeholder="Status"
            className="std-list-filter std-list-filter--sm"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'inactive', label: 'Inactive' },
            ]}
          />
        </div>
        <div className="std-list-actions">
          <Tooltip title={selectMode ? 'Done selecting' : 'Select multiple'}>
            <Button
              className="ui-iconbtn"
              icon={<SelectMultipleIcon />}
              type={selectMode ? 'primary' : 'default'}
              ghost={selectMode}
              aria-label={selectMode ? 'Done selecting' : 'Select multiple'}
              onClick={() => {
                if (selectMode) {
                  setSelectMode(false);
                  setSelectedKeys([]);
                } else {
                  setSelectMode(true);
                }
              }}
            />
          </Tooltip>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setModalOpen(true)}
            style={{ fontWeight: 700 }}
          >
            Add User
          </Button>
        </div>
      </div>

      {selectMode && (
        <div className="std-list-bulk">
          <span className="std-list-bulk__count">
            {selectedKeys.length ? `${selectedKeys.length} selected` : 'Select users'}
          </span>
          {selectedKeys.length > 0 && (
            <Button size="small" type="text" onClick={() => setSelectedKeys([])}>
              Clear
            </Button>
          )}
        </div>
      )}

      <ResponsiveTable
        card={{ subtitle: ['role'], badge: 'status', hide: ['email'] }}
        cardsBelow="narrow"
        className="std-list-table users-cards"
        rowKey="id"
        columns={columns}
        dataSource={filtered}
        loading={loading}
        locale={{ emptyText: 'No users match your filters yet' }}
        onRow={(r) => (selectMode ? {} : rowOpenProps(() => openEdit(r)))}
        size="small"
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: (total) => `${total} user${total === 1 ? '' : 's'}`,
        }}
        rowSelection={
          selectMode
            ? {
                selectedRowKeys: selectedKeys,
                onChange: (keys) => setSelectedKeys(keys.map(String)),
                preserveSelectedRowKeys: true,
              }
            : undefined
        }
      />

      <FormModal
        open={modalOpen}
        icon={<UserAddOutlined />}
        title="Add User"
        size="lg"
        width={640}
        onCancel={closeModal}
        onOk={() => form.submit()}
        okText="Create User"
        confirmLoading={submitting}
        destroyOnHidden
      >
        <Form scrollToFirstError={{ block: 'center' }}
          form={form}
          layout="vertical"
          onFinish={onCreate}
          requiredMark="optional"
          initialValues={{ role: 'PRODUCTION_WORKER' }}
        >
          <Form.Item name="role" label="Role" rules={[{ required: true }]} style={{ marginBottom: 14 }}>
            <Select
              options={[
                { value: 'ADMIN', label: 'Administrator' },
                { value: 'OFFICE_STAFF', label: 'Office Staff' },
                { value: 'PRODUCTION_WORKER', label: 'Production Worker' },
              ]}
            />
          </Form.Item>
          {sectionLabel('Account details')}
          <Row gutter={16}>
            <Col xs={24} sm={15}>
              <Form.Item
                name="fullName"
                label="Full Name"
                rules={[{ required: true, whitespace: true, message: 'Full name is required' }]}
                style={{ marginBottom: 14 }}
              >
                <Input placeholder="e.g. Juan Dela Cruz" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={9}>
              <Form.Item
                name="nickname"
                label="Nickname"
                style={{ marginBottom: 14 }}
              >
                <Input maxLength={40} placeholder="e.g. JD" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item
                name="email"
                label="Email"
                rules={[
                  { required: true, message: 'Email is required' },
                  { type: 'email', message: 'Enter a valid email' },
                ]}
                style={{ marginBottom: 14 }}
              >
                <Input />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="mobileNumber" label="Mobile number" style={{ marginBottom: 14 }}>
                <Input placeholder="09XX XXX XXXX" />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </FormModal>

      <FormModal
        open={!!editUser}
        icon={<EditOutlined />}
        title={editUser ? `Edit ${editUser.fullName}` : 'Edit user'}
        size="lg"
        width={640}
        onCancel={() => setEditUser(null)}
        onOk={() => editForm.submit()}
        okText="Save"
        confirmLoading={editSaving}
        forceRender
      >
        <Form scrollToFirstError={{ block: 'center' }} form={editForm} layout="vertical" onFinish={onEditSave} requiredMark="optional">
          {sectionLabel('Account details')}
          <Row gutter={16}>
            <Col xs={24} sm={15}>
              <Form.Item
                name="fullName"
                label="Full Name"
                rules={[{ required: true, whitespace: true, message: 'Full name is required' }]}
                style={{ marginBottom: 14 }}
              >
                <Input placeholder="e.g. Juan Dela Cruz" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={9}>
              <Form.Item
                name="nickname"
                label="Nickname"
                style={{ marginBottom: 14 }}
              >
                <Input maxLength={40} placeholder="e.g. JD" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item
                name="email"
                label="Email"
                rules={[
                  { required: true, message: 'Email is required' },
                  { type: 'email', message: 'Enter a valid email' },
                ]}
                style={{ marginBottom: 14 }}
              >
                <Input />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="mobileNumber" label="Mobile number" style={{ marginBottom: 14 }}>
                <Input placeholder="09XX XXX XXXX" />
              </Form.Item>
            </Col>
          </Row>
          {editUser?.status === 'INVITED' ? (
            <div style={{ fontSize: 12, color: '#64748b', margin: '-4px 0 12px' }}>
              Save, then send the setup link from the row menu.
            </div>
          ) : null}

          {sectionLabel('Role & access')}
          <Form.Item name="role" label="Role" rules={[{ required: true }]} style={{ marginBottom: 4 }}>
            <Select
              options={[
                { value: 'ADMIN', label: 'Administrator' },
                { value: 'OFFICE_STAFF', label: 'Office Staff' },
                { value: 'PRODUCTION_WORKER', label: 'Production Worker' },
              ]}
            />
          </Form.Item>
          {editUser && editRole && editRole !== editUser.role ? (
            <div style={{ fontSize: 12, color: '#b45309', marginTop: 8 }}>
              They will need to sign in again with their password.
            </div>
          ) : null}
        </Form>
      </FormModal>
    </div>
  );
}
