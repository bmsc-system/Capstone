import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Alert, Button, Drawer, Form, Grid, Input, Segmented, Spin, message } from 'antd';
import { EyeOutlined, SaveOutlined } from '@ant-design/icons';
import { shopDetailsApi, type ShopDetails } from '../../api/shopDetails.api';
import { getErrorMessage } from '../../api/client';
import { invalidateShopDetails } from '../../hooks/useShopDetails';
import { useUnsavedChangesWarning } from '../../hooks/useUnsavedChangesWarning';
import { usePageHeaderActions } from '../../layouts/pageHeaderActions';
import { formatShop } from '../../utils/shopTime';
import { shopLetterheadPreview } from './shopLetterheadPreview';
import EmailCheckCard from './EmailCheckCard';
import './shopDetails.css';

const EMPTY: ShopDetails = {
  shopName: '',
  tagline: '',
  address: '',
  telephone: '',
  mobileNumbers: '',
  email: '',
  poApproverName: '',
  poApproverTitle: '',
  joApproverName: '',
  joApproverTitle: '',
};

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <section className="sd-section">
      <header className="sd-section__head">
        <h2 className="sd-section__title">{title}</h2>
        <p className="sd-section__hint">{hint}</p>
      </header>
      <div className="sd-section__body">{children}</div>
    </section>
  );
}

type DocKind = 'PO' | 'JO';

/** A scaled-down page: letterhead, a sketch of the document, the signature line. */
function LetterheadPreviewSheet({ values, kind }: { values: Partial<ShopDetails>; kind: DocKind }) {
  const preview = shopLetterheadPreview(values);
  const approverName = kind === 'PO' ? preview.poApproverName : preview.joApproverName;
  const approverTitle = kind === 'PO' ? preview.poApproverTitle : preview.joApproverTitle;

  return (
    <div className="sd-paper" data-testid="letterhead-preview-sheet">
      <header className="shop-print-header sd-paper__header">
        <div className="shop-print-header__row">
          <img className="shop-print-logo" src="/logo.png" alt="" />
          <div className="shop-print-header__text">
            <div className="shop-print-name">{preview.shopName}</div>
            {preview.tagline ? <div className="shop-print-tagline">{preview.tagline}</div> : null}
          </div>
        </div>
        <div className="shop-print-bar" aria-hidden>
          {(['gold', 'black', 'gold', 'black'] as const).map((tone, i) => (
            <span key={i} className={`shop-print-bar__seg shop-print-bar__seg--${tone}`} />
          ))}
        </div>
      </header>
      <div className="sd-paper__doc" aria-hidden>
        <div className="sd-paper__doc-title">{kind === 'PO' ? 'PURCHASE ORDER' : 'JOB ORDER'}</div>
        <div className="sd-paper__doc-meta">
          <span className="sd-sketch" style={{ width: '38%' }} />
          <span className="sd-sketch" style={{ width: '22%' }} />
        </div>
        <div className="sd-paper__doc-table">
          {[78, 64, 70, 52].map((w, i) => (
            <div key={i} className="sd-paper__doc-row">
              <span className="sd-sketch" style={{ width: `${w}%` }} />
              <span className="sd-sketch" style={{ width: '12%' }} />
              <span className="sd-sketch" style={{ width: '14%' }} />
            </div>
          ))}
        </div>
      </div>

      <div className="sd-paper__sign">
        <div>Approved by:</div>
        <div className="sd-paper__sign-line" />
        <div className="shop-print-signature__name">{approverName}</div>
        <div>{approverTitle}</div>
      </div>

      <footer className="sd-paper__footer">
        <div className="shop-print-bar" aria-hidden>
          {(['gold', 'black', 'gold', 'black'] as const).map((tone, i) => (
            <span key={i} className={`shop-print-bar__seg shop-print-bar__seg--${tone}`} />
          ))}
        </div>
        {preview.address ? <div className="shop-print-address">{preview.address}</div> : null}
        {preview.contacts.length ? (
          <div className="shop-print-contacts">
            {preview.contacts.map((c, i) => (
              <span key={c}>
                {i > 0 ? <span className="shop-print-sep"> - </span> : null}
                {c}
              </span>
            ))}
          </div>
        ) : null}
      </footer>
    </div>
  );
}

function PreviewPanel({ values, dirty }: { values: Partial<ShopDetails>; dirty: boolean }) {
  const [kind, setKind] = useState<DocKind>('PO');
  return (
    <aside className="sd-preview" aria-label="Letterhead preview">
      <div className="sd-preview__head">
        <div>
          <h2 className="sd-preview__title">Preview</h2>
          <p className="sd-preview__caption">
            {dirty ? 'Showing your unsaved edits' : 'As it prints today'}
          </p>
        </div>
        <Segmented<DocKind>
          size="small"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'PO', label: 'Purchase order' },
            { value: 'JO', label: 'Job order' },
          ]}
        />
      </div>
      <div className="sd-preview__desk">
        <LetterheadPreviewSheet values={values} kind={kind} />
      </div>
    </aside>
  );
}

/** Admin-editable shop details printed on purchase orders and job orders. */
export default function ShopDetailsPage() {
  const screens = Grid.useBreakpoint();
  const isNarrow = !screens.lg;
  const [form] = Form.useForm<ShopDetails>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<ShopDetails | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerKind, setDrawerKind] = useState<DocKind>('PO');
  const watched = Form.useWatch([], form) as Partial<ShopDetails> | undefined;

  const dirty = useMemo(() => {
    if (!baseline || !watched) return false;
    return (Object.keys(EMPTY) as (keyof ShopDetails)[]).some(
      (k) => String(watched[k] ?? '') !== String(baseline[k] ?? '')
    );
  }, [baseline, watched]);

  useUnsavedChangesWarning(dirty);
  // Keep the app topbar free — Save lives with the form.
  usePageHeaderActions(null);

  useEffect(() => {
    shopDetailsApi
      .get()
      .then(({ data }) => {
        form.setFieldsValue(data);
        setBaseline(data);
        setUpdatedAt(data.updatedAt ?? null);
      })
      .catch((err) => setError(getErrorMessage(err)))
      .finally(() => setLoading(false));
  }, [form]);

  const onSave = async (values: ShopDetails) => {
    try {
      setSaving(true);
      const { data } = await shopDetailsApi.update(values);
      form.setFieldsValue(data);
      setBaseline(data);
      setUpdatedAt(data.updatedAt ?? null);
      invalidateShopDetails();
      message.success('Shop details saved');
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="page-spinner">
        <Spin />
      </div>
    );
  }

  const previewValues = watched || {};

  return (
    <div className="ui-page sd-page">
      {error ? <Alert type="error" showIcon title={error} className="sd-page__alert" /> : null}

      <div className="sd-page__layout">
        <Form scrollToFirstError={{ block: 'center' }}
          form={form}
          layout="vertical"
          onFinish={onSave}
          requiredMark="optional"
          className="sd-form"
        >
          <div className="sd-card">
            <Section title="Letterhead" hint="The name and line under it, at the top of every printed page.">
              <Form.Item
                name="shopName"
                label="Shop name"
                rules={[{ required: true, message: 'Shop name is required' }]}
              >
                <Input maxLength={255} />
              </Form.Item>
              <Form.Item name="tagline" label="Tagline">
                <Input.TextArea autoSize={{ minRows: 2, maxRows: 3 }} maxLength={500} />
              </Form.Item>
            </Section>

            <Section title="Contact" hint="Address and numbers printed at the foot of every page.">
              <Form.Item
                name="address"
                label="Address"
                rules={[{ required: true, message: 'Address is required' }]}
              >
                <Input maxLength={500} />
              </Form.Item>
              <div className="sd-pair">
                <Form.Item name="telephone" label="Telephone">
                  <Input maxLength={100} placeholder="(043) 4303524" />
                </Form.Item>
                <Form.Item name="mobileNumbers" label="Mobile numbers">
                  <Input maxLength={200} placeholder="0926… / 0915…" />
                </Form.Item>
              </div>
              <Form.Item name="email" label="Email" rules={[{ type: 'email', message: 'Enter a valid email' }]}>
                <Input maxLength={255} placeholder="shop@example.com" />
              </Form.Item>
            </Section>

            <Section title="Approvers" hint="Who signs each printed document.">
              <div className="sd-approver">
                <div className="sd-approver__doc">Purchase orders</div>
                <div className="sd-pair">
                  <Form.Item name="poApproverName" label="Name">
                    <Input maxLength={255} />
                  </Form.Item>
                  <Form.Item name="poApproverTitle" label="Title">
                    <Input maxLength={255} />
                  </Form.Item>
                </div>
              </div>
              <div className="sd-approver">
                <div className="sd-approver__doc">Job orders</div>
                <div className="sd-pair">
                  <Form.Item name="joApproverName" label="Name">
                    <Input maxLength={255} />
                  </Form.Item>
                  <Form.Item name="joApproverTitle" label="Title">
                    <Input maxLength={255} />
                  </Form.Item>
                </div>
              </div>
            </Section>

          </div>

          <div className={`sd-bar${dirty ? ' is-dirty' : ''}`}>
            <span className="sd-bar__status">
              {dirty ? (
                <>
                  <span className="sd-bar__dot" aria-hidden /> Unsaved changes
                </>
              ) : updatedAt ? (
                `Saved ${formatShop(updatedAt, 'MMM D, YYYY h:mm A')}`
              ) : (
                'Not saved yet'
              )}
            </span>
            <div className="sd-bar__actions">
              {isNarrow ? (
                <Button icon={<EyeOutlined />} onClick={() => setDrawerOpen(true)}>
                  Preview letterhead
                </Button>
              ) : null}
              {dirty && baseline ? (
                <Button type="text" onClick={() => form.setFieldsValue(baseline)}>
                  Discard
                </Button>
              ) : null}
              <Button
                type="primary"
                htmlType="submit"
                icon={<SaveOutlined />}
                loading={saving}
                disabled={!dirty}
              >
                Save
              </Button>
            </div>
          </div>
        </Form>

        {!isNarrow ? <PreviewPanel values={previewValues} dirty={dirty} /> : null}
      </div>

      <EmailCheckCard />

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="Letterhead preview"
        size="large"
        destroyOnHidden
        className="sd-drawer"
        extra={
          <Segmented<DocKind>
            size="small"
            value={drawerKind}
            onChange={setDrawerKind}
            options={[
              { value: 'PO', label: 'PO' },
              { value: 'JO', label: 'JO' },
            ]}
          />
        }
      >
        <div className="sd-preview__desk">
          <LetterheadPreviewSheet values={previewValues} kind={drawerKind} />
        </div>
      </Drawer>
    </div>
  );
}
