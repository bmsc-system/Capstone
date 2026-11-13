import { useEffect, useMemo, useState } from 'react';
import { Button, Input, Space, Select, Dropdown, message, Tooltip } from 'antd';
import type { MenuProps, TableColumnsType } from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  SearchOutlined,
  MoreOutlined,
} from '@ant-design/icons';
import { clientsApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import SelectMultipleIcon from '../../components/SelectMultipleIcon';
import { EllipsisText, ResponsiveTable, rowOpenProps } from '../../components/ui';
import { useAuth } from '../../hooks/useAuth';
import type { Client } from '../../types';
import { useNavigate } from 'react-router-dom';
import ClientFormModal from './ClientFormModal';

type NotifyFilter = 'email' | 'off';

function notifyLabel(r: Client) {
  return r.notifyByEmail ? 'Email' : 'Off';
}

export default function ClientsPage() {
  const navigate = useNavigate();
  const { canManageContacts: canEdit } = useAuth();
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [search, setSearch] = useState('');
  const [notifyFilter, setNotifyFilter] = useState<NotifyFilter[]>([]);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);

  const fetchClients = async () => {
    setLoading(true);
    try {
      const { data } = await clientsApi.list();
      setClients(data);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchClients();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return clients.filter((c) => {
      if (
        q &&
        !`${c.name} ${c.contact || ''} ${c.email || ''} ${c.mobileNumber || ''}`
          .toLowerCase()
          .includes(q)
      ) {
        return false;
      }
      if (notifyFilter.length) {
        const off = !c.notifyByEmail;
        const match = notifyFilter.some((f) => {
          if (f === 'email') return !!c.notifyByEmail;
          return off;
        });
        if (!match) return false;
      }
      return true;
    });
  }, [clients, search, notifyFilter]);

  const closeModal = () => setModalOpen(false);

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const openEdit = (c: Client) => {
    setEditing(c);
    setModalOpen(true);
  };

  const columns: TableColumnsType<Client> = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      sorter: (a, b) => a.name.localeCompare(b.name),
      ellipsis: true,
      render: (n: string, r: Client) => (
        <button
          type="button"
          className="client-name-link"
          title={n}
          onClick={() => navigate(`/clients/${r.id}`)}
          style={{
            fontWeight: 600,
            fontSize: 14,
            color: '#0f172a',
            background: 'none',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            textAlign: 'left',
            maxWidth: '100%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {n}
        </button>
      ),
    },
    {
      title: 'Contact',
      dataIndex: 'contact',
      key: 'contact',
      ellipsis: true,
      sorter: (a, b) => (a.contact || '').localeCompare(b.contact || ''),
      render: (v?: string) => <EllipsisText>{v || '—'}</EllipsisText>,
    },
    {
      title: 'Email',
      dataIndex: 'email',
      key: 'email',
      ellipsis: true,
      sorter: (a, b) => (a.email || '').localeCompare(b.email || ''),
      render: (v?: string) => <EllipsisText>{v || '—'}</EllipsisText>,
    },
    {
      title: 'Mobile',
      dataIndex: 'mobileNumber',
      key: 'mobileNumber',
      width: 140,
      ellipsis: true,
      sorter: (a, b) => (a.mobileNumber || '').localeCompare(b.mobileNumber || ''),
      render: (v?: string) => <EllipsisText>{v || '—'}</EllipsisText>,
    },
    {
      title: 'Notify',
      key: 'notify',
      width: 140,
      render: (_: unknown, r: Client) => (
        r.notifyByEmail ? (
          <StatusPill color="blue" compact>{notifyLabel(r)}</StatusPill>
        ) : (
          <span style={{ fontSize: 12, color: '#94a3b8' }}>Off</span>
        )
      ),
    },
    {
      title: '',
      key: 'actions',
      width: 56,
      align: 'center',
      render: (_: unknown, r: Client) => {
        const items: MenuProps['items'] = [
          {
            key: 'view',
            label: 'View history',
            onClick: () => navigate(`/clients/${r.id}`),
          },
          ...(canEdit
            ? [
                {
                  key: 'edit',
                  icon: <EditOutlined />,
                  label: 'Edit',
                  onClick: () => openEdit(r),
                },
              ]
            : []),
        ];
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

  return (
    <div className="std-list-page">
      <div className="std-list-toolbar">
        <div className="std-list-filters">
          <Input
            allowClear
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
            placeholder="Search name, contact, email, mobile…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="std-list-search"
          />
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            placeholder="Notify"
            className="std-list-filter std-list-filter--sm"
            value={notifyFilter}
            onChange={setNotifyFilter}
            options={[
              { value: 'email', label: 'Email on' },
              { value: 'off', label: 'Off' },
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
          {canEdit && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} style={{ fontWeight: 700 }}>
              Register client
            </Button>
          )}
        </div>
      </div>

      {selectMode && (
        <div className="std-list-bulk">
          <span className="std-list-bulk__count">
            {selectedKeys.length ? `${selectedKeys.length} selected` : 'Select clients'}
          </span>
          <Space size={8}>
            {selectedKeys.length > 0 && (
              <Button size="small" type="text" onClick={() => setSelectedKeys([])}>
                Clear
              </Button>
            )}
          </Space>
        </div>
      )}

      <ResponsiveTable
        card={{ subtitle: ['contact'], badge: 'notify' }}
        cardsBelow="narrow"
        className="std-list-table"
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={filtered}
        columns={columns}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: (total) => `${total} client${total === 1 ? '' : 's'}`,
        }}
        locale={{ emptyText: 'No clients match your filters yet' }}
        onRow={(r) =>
          rowOpenProps(() =>
            selectMode
              ? setSelectedKeys((keys) => (keys.includes(r.id) ? keys.filter((k) => k !== r.id) : [...keys, r.id]))
              : navigate(`/clients/${r.id}`)
          )
        }
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

      <ClientFormModal
        open={modalOpen}
        client={editing}
        onClose={closeModal}
        onSaved={() => {
          setModalOpen(false);
          fetchClients();
        }}
      />
    </div>
  );
}
