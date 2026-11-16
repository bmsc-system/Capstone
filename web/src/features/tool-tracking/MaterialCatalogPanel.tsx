import { useEffect, useMemo, useState } from 'react';
import { Button, Col, Form, Input, Modal, Row, Select, Segmented, Switch, message } from 'antd';
import { ChipOverflow, FilterBar, FilterSelect, TwoLineCell, rowOpenProps } from '../../components/ui';
import { MOBILE_SHELL_QUERY, NARROW_QUERY, PHONE_QUERY, useMediaQuery } from '../../hooks/useViewport';
import type { TableColumnsType } from 'antd';
import { EditOutlined, PlusOutlined, SearchOutlined, BookOutlined } from '@ant-design/icons';
import { BookOpen, Package, Layers } from 'lucide-react';
import {
  CONSUMABLE_UNITS,
  MATERIAL_UNITS,
  catalogApi,
  catalogMatches,
  type CatalogCreateInput,
  type CatalogItem,
  type CatalogKind,
  type CatalogTypeFilter,
} from '../../api/catalog.api';
import { getErrorMessage } from '../../api/client';
import { invalidateMaterialCatalog } from '../../hooks/useMaterialCatalog';
import StatusPill from '../../components/StatusPill';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { ADD_CATALOG_ITEM_LABEL } from './inventoryTabs';
import {
  CategoryTile,
  InventorySummaryCards,
  type InventorySummaryCard,
} from './inventoryLook';
import { newestFirst } from '../../utils/newestFirst';

function sectionLabel(text: string) {
  return <div className="app-form-section">{text}</div>;
}

/** Unified buy-list: the materials and consumables the shop buys. Consumables are
 *  not counted; their purchases are on the Consumables tab. */
export default function MaterialCatalogPanel({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<CatalogItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [materialUnits, setMaterialUnits] = useState<string[]>(MATERIAL_UNITS);
  const [consumableUnits, setConsumableUnits] = useState<string[]>(CONSUMABLE_UNITS);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<CatalogTypeFilter>('all');
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const [summaryKey, setSummaryKey] = useState<string | null>(null);
  const kindWatch = Form.useWatch('kind', form) as CatalogKind | undefined;
  const compact = useMediaQuery(MOBILE_SHELL_QUERY);
  const narrow = useMediaQuery(NARROW_QUERY);
  const phone = useMediaQuery(PHONE_QUERY);

  const fetchRows = async () => {
    setLoading(true);
    try {
      const { data } = await catalogApi.list({ includeInactive: true });
      setRows(newestFirst(data.items));
      setCategories(data.categories);
      setMaterialUnits(data.materialUnits?.length ? data.materialUnits : MATERIAL_UNITS);
      setConsumableUnits(data.consumableUnits?.length ? data.consumableUnits : CONSUMABLE_UNITS);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchRows();
  }, []);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (typeFilter === 'MATERIAL' && r.kind !== 'MATERIAL') return false;
      if (typeFilter === 'CONSUMABLE' && r.kind !== 'CONSUMABLE') return false;
      if (summaryKey === 'materials' && r.kind !== 'MATERIAL') return false;
      if (summaryKey === 'consumables' && r.kind !== 'CONSUMABLE') return false;
      // Archived items stay on past records but only show under Archived.
      if (summaryKey === 'archived' ? r.active : !r.active) return false;
      return catalogMatches(r, query);
    });
  }, [rows, query, typeFilter, summaryKey]);

  const catalogCards: InventorySummaryCard[] = useMemo(() => {
    const materials = rows.filter((r) => r.active && r.kind === 'MATERIAL').length;
    const consumables = rows.filter((r) => r.active && r.kind === 'CONSUMABLE').length;
    const archived = rows.filter((r) => !r.active).length;
    return [
      { key: 'materials', label: 'Materials', value: materials, icon: Layers, tone: 'blue' },
      { key: 'consumables', label: 'Consumables', value: consumables, icon: Package, tone: 'slate' },
      { key: 'archived', label: 'Archived', value: archived, icon: BookOpen, tone: 'red' },
    ];
  }, [rows]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      kind: 'MATERIAL',
      defaultUnit: 'pcs',
      unit: 'pcs',
      active: true,
      orderedFromSupplier: false,
    });
    setModalOpen(true);
  };

  const openEdit = (r: CatalogItem) => {
    setEditing(r);
    form.setFieldsValue({
      kind: r.kind,
      name: r.name,
      shopTerm: r.shopTerm,
      sizeSpec: r.kind === 'CONSUMABLE' ? r.sizeSpec || r.gradesOrSizes?.[0] || undefined : undefined,
      defaultUnit: r.defaultUnit || r.unit,
      unit: r.unit,
      category: r.category,
      active: r.active,
      orderedFromSupplier: !!r.orderedFromSupplier,
    });
    setModalOpen(true);
  };

  const onSave = async (values: CatalogCreateInput & { sizeSpec?: string }) => {
    try {
      setSaving(true);
      if (editing) {
        const body = {
          name: values.name,
          shopTerm: values.shopTerm,
          active: values.active,
          category: values.category,
          ...(editing.kind === 'MATERIAL'
            ? { defaultUnit: values.defaultUnit || values.unit }
            : { sizeSpec: values.sizeSpec || null, unit: values.unit, orderedFromSupplier: !!values.orderedFromSupplier }),
        };
        if (editing.kind === 'MATERIAL') {
          await catalogApi.updateMaterial(editing.id, body);
        } else {
          await catalogApi.updateConsumable(editing.id, body);
        }
        message.success('Catalog item updated');
      } else {
        const kind = values.kind;
        await catalogApi.create({
          kind,
          name: values.name,
          shopTerm: values.shopTerm,
          active: values.active ?? true,
          category: kind === 'MATERIAL' ? values.category : undefined,
          ...(kind === 'MATERIAL'
            ? { defaultUnit: values.defaultUnit || 'pcs' }
            : {
                sizeSpec: values.sizeSpec || null,
                unit: values.unit || 'pcs',
                orderedFromSupplier: !!values.orderedFromSupplier,
              }),
        });
        message.success('Catalog item added');
      }
      invalidateMaterialCatalog();
      setModalOpen(false);
      await fetchRows();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const gradesOf = (r: CatalogItem) =>
    r.gradesOrSizes?.length ? r.gradesOrSizes : r.grades || (r.sizeSpec ? [r.sizeSpec] : []);

  const statusCell = (r: CatalogItem) => {
    const label = r.active ? 'In use' : 'Archived';
    const color = r.active ? 'green' : 'gray';
    return (
      <div className="catalog-status">
        <StatusPill color={color} compact>
          {label}
        </StatusPill>
      </div>
    );
  };

  const cardColumns: TableColumnsType<CatalogItem> = [
    {
      title: 'Item',
      key: 'name',
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (_: unknown, r) => {
        const kindLabel = r.kind === 'MATERIAL' ? 'Material' : 'Consumable';
        const meta = [
          kindLabel,
          r.category && r.category.toLowerCase() !== kindLabel.toLowerCase() ? r.category : null,
          r.unit,
        ]
          .filter(Boolean)
          .join(' · ');
        const extra = [r.kind === 'CONSUMABLE' && r.code ? r.code : null, r.shopTerm ? `Shop term “${r.shopTerm}”` : null]
          .filter(Boolean)
          .join(' · ');
        return (
          <div className="catalog-item">
            <CategoryTile category={r.category || r.name} size={36} />
            <div className="catalog-item__text">
              <div className="catalog-item__name">{r.name}</div>
              <div className="catalog-item__meta">{meta}</div>
              {extra ? <div className="catalog-item__meta">{extra}</div> : null}
            </div>
          </div>
        );
      },
    },
    {
      title: 'Category',
      key: 'category',
      sorter: (a, b) => (a.category || '').localeCompare(b.category || ''),
      render: () => null,
    },
    {
      title: 'Size',
      key: 'grades',
      render: (_: unknown, r) => {
        const list = gradesOf(r);
        return list.length ? <ChipOverflow items={list} max={4} plain /> : null;
      },
    },
    { title: 'Status', key: 'status', render: (_: unknown, r) => statusCell(r) },
  ];

  const columns: TableColumnsType<CatalogItem> = [
    {
      title: 'Item',
      key: 'name',
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (_: unknown, r) => {
        const secondary = [
          r.shopTerm ? `Shop term: “${r.shopTerm}”` : null,
          r.kind === 'CONSUMABLE' && r.code ? `Code: ${r.code}` : null,
          r.kind === 'CONSUMABLE' && r.orderedFromSupplier ? 'Ordered from a supplier' : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
            <CategoryTile category={r.category || r.name} />
            <TwoLineCell primary={r.name} secondary={secondary || null} />
          </div>
        );
      },
    },
    {
      title: 'Type',
      dataIndex: 'kind',
      width: 120,
      render: (kind: CatalogKind) =>
        kind === 'MATERIAL' ? (
          <StatusPill tone="blue" plain>
            Material
          </StatusPill>
        ) : (
          <StatusPill tone="indigo" plain>
            Consumable
          </StatusPill>
        ),
    },
    {
      title: 'Size',
      key: 'grades',
      ellipsis: true,
      render: (_: unknown, r) => <ChipOverflow items={gradesOf(r)} max={2} plain />,
    },
    { title: 'Unit', dataIndex: 'unit', width: 80 },
    {
      title: 'Category',
      dataIndex: 'category',
      width: 130,
      sorter: (a, b) => (a.category || '').localeCompare(b.category || ''),
      render: (v: string | null) => v || '—',
    },
    {
      title: 'Status',
      key: 'status',
      width: 148,
      render: (_: unknown, r) => statusCell(r),
    },
    ...(canEdit
      ? [
          {
            title: '',
            key: 'actions',
            width: 56,
            render: (_: unknown, r: CatalogItem) => (
              <Button type="text" icon={<EditOutlined />} onClick={() => openEdit(r)} />
            ),
          },
        ]
      : []),
  ];

  const kind = editing?.kind || kindWatch || 'MATERIAL';
  const isMaterial = kind === 'MATERIAL';

  return (
    <div className="std-list-page">
      <InventorySummaryCards
        cards={catalogCards}
        activeKey={summaryKey}
        onSelect={(key) => {
          setSummaryKey(key);
          if (key === 'materials') setTypeFilter('MATERIAL');
          else if (key === 'consumables') setTypeFilter('CONSUMABLE');
          else if (key === null) setTypeFilter('all');
        }}
      />
      {compact ? (
        <FilterBar
          search={{ value: query, onChange: setQuery, placeholder: 'Search name or shop term…' }}
          activeFilters={typeFilter === 'all' ? 0 : 1}
          onClearFilters={() => {
            setTypeFilter('all');
            setSummaryKey(null);
          }}
          filters={
            <FilterSelect<CatalogTypeFilter>
              label="Type"
              style={phone ? undefined : { width: 200, flexBasis: 200 }}
              value={typeFilter}
              onChange={(v) => setTypeFilter(v ?? 'all')}
              options={[
                { label: 'All', value: 'all' },
                { label: 'Materials', value: 'MATERIAL' },
                { label: 'Consumables', value: 'CONSUMABLE' },
              ]}
            />
          }
          actions={
            canEdit ? (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={openCreate}
                aria-label={ADD_CATALOG_ITEM_LABEL}
                style={{ fontWeight: 700 }}
              >
                {phone ? null : ADD_CATALOG_ITEM_LABEL}
              </Button>
            ) : null
          }
        />
      ) : (
      <div className="std-list-toolbar">
        <div className="std-list-filters">
          <Input
            allowClear
            placeholder="Search name, shop term, code…"
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="std-list-search"
          />
          <Segmented
            value={typeFilter}
            onChange={(v) => setTypeFilter(v as CatalogTypeFilter)}
            options={[
              { label: 'All', value: 'all' },
              { label: 'Materials', value: 'MATERIAL' },
              { label: 'Consumables', value: 'CONSUMABLE' },
            ]}
          />
        </div>
        <div className="std-list-actions">
          {canEdit && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} style={{ fontWeight: 700 }}>
              {ADD_CATALOG_ITEM_LABEL}
            </Button>
          )}
        </div>
      </div>
      )}

      <ResponsiveTable
        className="std-list-table"
        rowKey={(r) => `${r.kind}-${r.id}`}
        size="small"
        loading={loading}
        columns={narrow ? cardColumns : columns}
        cardsBelow="narrow"
        card={{ title: 'name', badge: 'status', actions: 'none', hide: ['category'], full: ['grades'] }}
        onRow={narrow && canEdit ? (r) => rowOpenProps(() => openEdit(r)) : undefined}
        dataSource={filtered}
        scroll={{ x: 880 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{
          emptyText: rows.length
            ? 'No catalog items match your search.'
            : 'No items in the catalog yet.',
        }}
      />

      <Modal
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        width={600}
        centered
        destroyOnHidden
        className="app-form-modal"
        styles={{
          container: { padding: 0, borderRadius: 0, overflow: 'hidden' },
          body: { padding: 0 },
        }}
        closable={false}
      >
        <div className="app-form-modal__head">
          <div className="app-form-modal__icon">
            <BookOutlined />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="app-form-modal__title">
              {editing ? 'Edit catalog item' : ADD_CATALOG_ITEM_LABEL}
            </div>
          </div>
          <button
            type="button"
            className="app-form-modal__close"
            onClick={() => setModalOpen(false)}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <Form scrollToFirstError={{ block: 'center' }} form={form} layout="vertical" onFinish={onSave} style={{ padding: '20px 24px 8px' }}>
          {sectionLabel('Type')}
          <Form.Item name="kind" label="Type" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(editing)}
              options={[
                { value: 'MATERIAL', label: 'Material' },
                { value: 'CONSUMABLE', label: 'Consumable' },
              ]}
            />
          </Form.Item>
          {sectionLabel(isMaterial ? 'Material' : 'Consumable')}
          <Row gutter={12}>
            <Col xs={24} md={14}>
              <Form.Item name="name" label="Name" rules={[{ required: true }]}>
                <Input
                  placeholder={
                    isMaterial ? 'e.g. 1045' : 'e.g. Cutting Disk'
                  }
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={10}>
              <Form.Item
                name="shopTerm"
                label="Shop term"
                tooltip="What the shop calls it; searching by it finds this entry."
              >
                <Input placeholder={isMaterial ? 'e.g. 10-45' : 'e.g. Liha'} />
              </Form.Item>
            </Col>
          </Row>
          {isMaterial ? null : (
            <Form.Item name="sizeSpec" label="Size / spec">
              <Input placeholder='e.g. 4" (107 mm)' />
            </Form.Item>
          )}
          <Row gutter={12}>
            <Col xs={12} md={8}>
              {isMaterial ? (
                <Form.Item name="defaultUnit" label="Default unit" rules={[{ required: true }]}>
                  <Select options={materialUnits.map((u) => ({ value: u, label: u }))} />
                </Form.Item>
              ) : (
                <Form.Item name="unit" label="Unit" rules={[{ required: true }]}>
                  <Select options={consumableUnits.map((u) => ({ value: u, label: u }))} />
                </Form.Item>
              )}
            </Col>
            {isMaterial ? (
              <Col xs={12} md={10}>
                <Form.Item name="category" label="Category">
                  <Select allowClear options={categories.map((c) => ({ value: c, label: c }))} />
                </Form.Item>
              </Col>
            ) : null}
            <Col xs={isMaterial ? 24 : 12} md={isMaterial ? 6 : 8}>
              <Form.Item
                name="active"
                label="In use"
                valuePropName="checked"
                tooltip="Turn off to archive an item the shop no longer buys. It leaves the pickers and lists, and past records keep it."
              >
                <Switch checkedChildren="Yes" unCheckedChildren="No" />
              </Form.Item>
            </Col>
            {isMaterial ? null : (
              <Col xs={12} md={16}>
                <Form.Item
                  name="orderedFromSupplier"
                  label="Ordered from a supplier"
                  valuePropName="checked"
                  tooltip="Most consumables are bought in cash at a nearby store. Turn this on only for items ordered through a supplier purchase order, such as cutting oil by the gallon."
                >
                  <Switch checkedChildren="Yes" unCheckedChildren="No" />
                </Form.Item>
              </Col>
            )}
          </Row>
        </Form>
        <div className="app-form-modal__footer">
          <Button onClick={() => setModalOpen(false)} style={{ minWidth: 96 }}>
            Cancel
          </Button>
          <Button
            type="primary"
            loading={saving}
            onClick={() => form.submit()}
            style={{ fontWeight: 700, minWidth: 120 }}
          >
            Save
          </Button>
        </div>
      </Modal>
    </div>
  );
}
