import { useEffect, useRef, useState } from 'react';
import {
  Form,
  Input,
  InputNumber,
  Button,
  DatePicker,
  Select,
  Alert,
  Row,
  Col,
  Checkbox,
} from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { clientsApi, jobOrdersApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { isPastShopDay } from '../../utils/shopTime';
import type { Client, JobOrderStatus } from '../../types';
import { jobOrdersDraftsListPath } from './jobOrderListPaths';
import { IdChip, Page, SectionCard, PageSkeleton } from '../../components/ui';
import PlannedMaterialsEditor from './PlannedMaterialsEditor';
import { blankPlannedRow, plannedPayload, rowsFromJob, type PlannedRow } from './plannedMaterials';
import './joForm.css';
import { showFormError, validateOrShow } from '../../utils/formFeedback';

const { TextArea } = Input;

type MaterialsNeeded = 'TO_ORDER' | 'NOT_REQUIRED';

/** Fabrication and Machining need stock bought for them; Repair works on the client's part. */
const defaultMaterialsNeeded = (jobType?: string): MaterialsNeeded =>
  jobType === 'REPAIR' ? 'NOT_REQUIRED' : 'TO_ORDER';

export default function JobOrderFormPage() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const fromPlanning = searchParams.get('from') === 'plan';
  const presetClientId = !id ? searchParams.get('client') : null;
  const [form] = Form.useForm();
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(isEdit);
  const [submitting, setSubmitting] = useState(false);
  // Checking the form takes a moment before `submitting` turns on; a second
  // click in that gap used to create the job order twice.
  const savingRef = useRef(false);
  const saveJob = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    try {
      await saveJobOnce();
    } finally {
      savingRef.current = false;
    }
  };
  const [error, setError] = useState('');
  const [jobNumber, setJobNumber] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobOrderStatus | null>(null);
  const [materialsTouched, setMaterialsTouched] = useState(false);
  const [loadedValues, setLoadedValues] = useState<Record<string, unknown> | null>(null);
  const [materials, setMaterials] = useState<PlannedRow[]>(() => (isEdit ? [] : [blankPlannedRow()]));
  const [materialErrorKey, setMaterialErrorKey] = useState<number | null>(null);
  const materialsNeeded = Form.useWatch('materialsNeeded', form) as MaterialsNeeded | undefined;
  const { isAdmin, canOfficeWrite } = useAuth();
  const released = jobStatus != null && jobStatus !== 'DRAFT';
  /** Edit job info: Office Staff only, and only before release. */
  const backTo = !id
    ? presetClientId
      ? `/clients/${presetClientId}`
      : jobOrdersDraftsListPath()
    : fromPlanning
      ? `/job-orders/${id}/plan`
      : `/job-orders/${id}`;
  const pageHeading = isEdit ? 'Edit Job Information' : 'New Job Order';

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await clientsApi.list();
        if (!cancelled) setClients(data);
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isEdit || !id) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const { data } = await jobOrdersApi.get(id);
        if (cancelled) return;
        setJobNumber(data.jobNumber || data.id.slice(0, 8).toUpperCase());
        setJobStatus(data.status);
        setMaterialsTouched(true);
        setMaterials(rowsFromJob(data.rawMaterials, data.plannedMaterials));
        setLoadedValues({
          clientId: data.clientId,
          title: data.title,
          description: data.description,
          dueDate: data.dueDate ? dayjs(data.dueDate) : undefined,
          clientPoNumber: data.clientPoNumber,
          jobType: data.jobType || 'FABRICATION',
          quantity: data.quantity,
          unitOfMeasure: data.unitOfMeasure,
          amount: data.amount,
          materialsNeeded: data.materialStatus === 'NOT_REQUIRED' ? 'NOT_REQUIRED' : 'TO_ORDER',
        });
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, isEdit]);

  useEffect(() => {
    if (!presetClientId || form.getFieldValue('clientId')) return;
    if (clients.some((c) => c.id === presetClientId)) form.setFieldValue('clientId', presetClientId);
  }, [clients, form, presetClientId]);

  // The <Form> only mounts once loading ends, so fill it after that render.
  useEffect(() => {
    if (loading || !loadedValues) return;
    form.setFieldsValue(loadedValues);
    setLoadedValues(null);
  }, [form, loading, loadedValues]);

  const saveJobOnce = async () => {
    let values: {
      clientId: string;
      title: string;
      description?: string;
      dueDate: dayjs.Dayjs;
      clientPoNumber?: string;
      jobType: string;
      quantity?: number;
      unitOfMeasure?: string;
      amount?: number;
      materialsNeeded: MaterialsNeeded;
    };
    try {
      values = await validateOrShow(form);
    } catch {
      return;
    }

    const planned =
      values.materialsNeeded === 'TO_ORDER' ? plannedPayload(materials) : { lines: [] };
    if (planned.error) {
      setMaterialErrorKey(planned.error.key);
      showFormError(planned.error.message, document.querySelector('.jo-form__panel--mats'));
      return;
    }
    setMaterialErrorKey(null);

    setSubmitting(true);
    setError('');
    const payload = {
      clientId: values.clientId,
      title: values.title,
      description: values.description,
      dueDate: values.dueDate.format('YYYY-MM-DD'),
      clientPoNumber: values.clientPoNumber || null,
      jobType: values.jobType,
      quantity: values.quantity ?? null,
      unitOfMeasure: values.unitOfMeasure || null,
      amount: values.amount ?? null,
      materialStatus: values.materialsNeeded,
      rawMaterials: planned.lines,
    };

    try {
      if (isEdit && id) {
        await jobOrdersApi.update(id, payload);
        navigate(backTo);
      } else {
        const { data } = await jobOrdersApi.create(payload);
        navigate(`/job-orders/${data.id}`);
      }
    } catch (err) {
      showFormError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <PageSkeleton variant="detail" />
    );
  }

  if (isEdit && id && (released || !canOfficeWrite)) {
    return <Navigate to={`/job-orders/${id}`} replace />;
  }

  if (!isEdit && !canOfficeWrite) {
    return <Navigate to={jobOrdersDraftsListPath()} replace />;
  }

  const needsMaterials = (materialsNeeded ?? 'TO_ORDER') === 'TO_ORDER';

  return (
    <Page className="jo-form-page jo-form-page--split">
      <SectionCard
        className="jo-form-card"
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            {pageHeading}
            {isEdit && jobNumber ? <IdChip>{jobNumber}</IdChip> : null}
          </span>
        }
      >
        {error && (
          <Alert type="error" title={error} style={{ marginBottom: 12 }} showIcon closable onClose={() => setError('')} />
        )}

        <Form
          form={form}
          layout="vertical"
          className="jo-form"
          requiredMark
          initialValues={{
            jobType: 'FABRICATION',
            materialsNeeded: defaultMaterialsNeeded('FABRICATION'),
          }}
          onValuesChange={(changed) => {
            if ('materialsNeeded' in changed) setMaterialsTouched(true);
            if ('jobType' in changed && !materialsTouched) {
              form.setFieldValue('materialsNeeded', defaultMaterialsNeeded(changed.jobType));
            }
          }}
        >
          <div className="jo-form__split">
            <section className="jo-form__panel" aria-label="Job details">
              <div className="ui-form-section">Job details</div>
              <Row gutter={[16, 0]}>
                <Col xs={24} md={15}>
                  <Form.Item name="clientId" label="Client" rules={[{ required: true, message: 'Choose the client' }]}>
                    <Select
                      showSearch
                      optionFilterProp="label"
                      options={clients.map((c) => ({ value: c.id, label: c.name }))}
                      placeholder="Select client"
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={9}>
                  <Form.Item name="clientPoNumber" label="Client PO #">
                    <Input placeholder="From the client's purchase order" />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item name="title" label="Title" rules={[{ required: true, message: 'Enter a title' }]}>
                <Input placeholder="e.g. Machining of Cyclodrive Base" />
              </Form.Item>
              <Row gutter={[16, 0]}>
                <Col xs={24} sm={12}>
                  <Form.Item name="jobType" label="Job type" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { value: 'FABRICATION', label: 'Fabrication' },
                        { value: 'MACHINING', label: 'Machining' },
                        { value: 'REPAIR', label: 'Repair' },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="dueDate" label="Date required" rules={[{ required: true, message: 'Pick the date required' }]}>
                    <DatePicker style={{ width: '100%' }} format="MMM D, YYYY" disabledDate={isPastShopDay} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={[16, 0]}>
                <Col xs={12} sm={8}>
                  <Form.Item name="quantity" label="Quantity">
                    <InputNumber style={{ width: '100%' }} min={0} step={1} placeholder="1" />
                  </Form.Item>
                </Col>
                <Col xs={12} sm={8}>
                  <Form.Item name="unitOfMeasure" label="Unit">
                    <Select
                      allowClear
                      placeholder="pcs"
                      options={[
                        { value: 'pcs', label: 'pcs' },
                        { value: 'lot', label: 'lot' },
                        { value: 'set', label: 'set' },
                        { value: 'kg', label: 'kg' },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={8}>
                  <Form.Item name="amount" label="Amount (PHP)">
                    <InputNumber style={{ width: '100%' }} min={0} step={0.01} placeholder="0.00" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="description" label="Description" className="jo-form__notes">
                <TextArea
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  placeholder="Notes from the PO or special instructions (optional)"
                />
              </Form.Item>
            </section>

            <section className="jo-form__panel jo-form__panel--mats" aria-label="Materials needed">
              <div className="ui-form-section">Materials needed</div>
              <Form.Item
                name="materialsNeeded"
                valuePropName="checked"
                getValueProps={(v) => ({ checked: v === 'NOT_REQUIRED' })}
                normalize={(checked: boolean) => (checked ? 'NOT_REQUIRED' : 'TO_ORDER')}
                className="jo-form__materials-item"
              >
                <Checkbox disabled={released && !isAdmin && needsMaterials}>
                  No materials to buy{' '}
                  <span className="ui-muted-note">(client-supplied or in stock)</span>
                </Checkbox>
              </Form.Item>
              {needsMaterials ? (
                <>
                  <PlannedMaterialsEditor
                    rows={materials}
                    errorKey={materialErrorKey}
                    onChange={(next) => {
                      setMaterials(next);
                      if (materialErrorKey != null) setMaterialErrorKey(null);
                    }}
                  />
                </>
              ) : (
                <div className="jo-mats__none">
                  <CheckOutlined /> Nothing will be ordered for this job.
                </div>
              )}
            </section>
          </div>

          <div className="jo-form__footer m-action-bar">
            <span className="m-action-bar__spacer" aria-hidden />
            <Button onClick={() => navigate(backTo)}>Cancel</Button>
            <Button type="primary" loading={submitting} onClick={saveJob} className="jo-form__save">
              {isEdit ? 'Save changes' : 'Create job order'}
            </Button>
          </div>
        </Form>
      </SectionCard>
    </Page>
  );
}
