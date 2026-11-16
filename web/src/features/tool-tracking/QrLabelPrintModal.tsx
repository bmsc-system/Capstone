import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Col, InputNumber, QRCode, Row, Select, Tooltip } from 'antd';
import { InfoCircleOutlined, LeftOutlined, PrinterOutlined, QrcodeOutlined, RightOutlined } from '@ant-design/icons';
import { FormModal } from '../../components/ui';
import {
  LABEL_SIZES,
  PAPERS,
  layoutSheets,
  perSheet,
  sheetGrid,
  sizeHint,
  type LabelSize,
  type LabelSizeId,
  type Paper,
  type PaperId,
  type QrLabelItem,
} from './qrLabels';
import './qrLabels.css';
import labelCss from './qrLabels.css?raw';

const PAD = 1.6; // mm, inside each label
const LARGE_TEXT = 20; // mm under the QR on large labels

const SMALL_TEXT: Record<LabelSizeId, boolean> = { xsmall: true, small: false, medium: false, large: false };

function qrSide(size: LabelSize) {
  return size.id === 'large' ? size.height - PAD * 2 - LARGE_TEXT : size.height - PAD * 2;
}

function Sheet({
  cells,
  size,
  paper,
  cutLines,
}: {
  cells: (QrLabelItem | null)[];
  size: LabelSize;
  paper: Paper;
  cutLines: boolean;
}) {
  const side = qrSide(size);
  return (
    <div className="qr-sheet" style={{ width: `${paper.width}mm`, height: `${paper.height}mm` }}>
      {cells.map((item, i) => {
        if (!item) return null;
        const col = i % size.cols;
        const row = Math.floor(i / size.cols);
        return (
          <div
            key={`${item.key}-${i}`}
            className={`qr-label qr-label--${size.id}${cutLines ? ' qr-label--cut' : ''}`}
            style={{
              left: `${size.left + col * (size.width + size.colGap)}mm`,
              top: `${size.top + row * (size.height + size.rowGap)}mm`,
              width: `${size.width}mm`,
              height: `${size.height}mm`,
            }}
          >
            <div className="qr-label__qr" style={{ width: `${side}mm`, height: `${side}mm` }}>
              <QRCode value={item.code} type="svg" size={256} bordered={false} errorLevel="M" />
            </div>
            <div className="qr-label__text">
              <span className="qr-label__code">{item.code}</span>
              <span className="qr-label__name">{item.name}</span>
              {item.detail && !SMALL_TEXT[size.id] ? <span className="qr-label__detail">{item.detail}</span> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Every sheet in a scrolling column, with a page counter and arrows. */
function SheetPreview({
  sheets,
  size,
  paper,
  scale,
}: {
  sheets: (QrLabelItem | null)[][];
  size: LabelSize;
  paper: Paper;
  scale: number;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const count = sheets.length;
  const current = Math.min(page, count - 1);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
    setPage(0);
  }, [count, size.id, paper.id]);

  const sheetEls = () => Array.from(scrollRef.current?.querySelectorAll<HTMLElement>('.qr-preview__frame') ?? []);
  const go = (i: number) => {
    const el = sheetEls()[i];
    const box = scrollRef.current;
    if (!el || !box) return;
    box.scrollTo({ top: el.offsetTop - box.offsetTop - 12, behavior: 'smooth' });
  };
  const onScroll = () => {
    const box = scrollRef.current;
    if (!box) return;
    const mid = box.scrollTop + box.clientHeight / 2;
    const els = sheetEls();
    let at = 0;
    els.forEach((el, i) => {
      if (el.offsetTop - box.offsetTop <= mid) at = i;
    });
    setPage(at);
  };

  return (
    <div className="qr-preview">
      <div className="qr-preview__nav">
        <Button
          size="small"
          type="text"
          icon={<LeftOutlined />}
          aria-label="Previous sheet"
          disabled={current <= 0}
          onClick={() => go(current - 1)}
        />
        <span>
          Sheet {current + 1} of {count}
        </span>
        <Button
          size="small"
          type="text"
          icon={<RightOutlined />}
          aria-label="Next sheet"
          disabled={current >= count - 1}
          onClick={() => go(current + 1)}
        />
      </div>
      <div
        ref={scrollRef}
        className="qr-preview__scroll"
        style={{ height: `calc(${Math.min(paper.height, 297)}mm * ${scale} + 24px)` }}
        onScroll={onScroll}
        aria-label="Preview of the sheets"
      >
        {sheets.map((cells, i) => (
          <div
            key={i}
            className="qr-preview__frame"
            style={{ width: `calc(${paper.width}mm * ${scale})`, height: `calc(${paper.height}mm * ${scale})` }}
          >
            <div style={{ transform: `scale(${scale})`, transformOrigin: 'top left' }}>
              {/* The preview always outlines labels so their places are visible. */}
              <Sheet cells={cells} size={size} paper={paper} cutLines />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Print QR labels for the chosen items on A4 label sheets (or plain A4 paper). */
export default function QrLabelPrintModal({
  open,
  items,
  onClose,
  title = 'Print QR labels',
}: {
  open: boolean;
  items: QrLabelItem[];
  onClose: () => void;
  title?: string;
}) {
  const [sizeId, setSizeId] = useState<LabelSizeId>('medium');
  const [paperId, setPaperId] = useState<PaperId>('a4');
  const [copies, setCopies] = useState(1);
  const [startAt, setStartAt] = useState(1);
  const [cutLines, setCutLines] = useState(false);
  const [printing, setPrinting] = useState(false);

  const paper = PAPERS[paperId];
  const size = useMemo(() => sheetGrid(LABEL_SIZES[sizeId], paper), [sizeId, paper]);
  const per = perSheet(size);
  const sheets = useMemo(
    () => layoutSheets(items, size, copies, startAt),
    [items, size, copies, startAt]
  );
  const labelCount = items.length * Math.max(1, copies);

  useEffect(() => {
    if (startAt > per) setStartAt(1);
  }, [per, startAt]);

  const printRef = useRef<HTMLDivElement>(null);

  /** Print from a separate, hidden frame holding only the sheets, so the page
   * layout (and its own print styles) cannot change what comes out. */
  const print = () => {
    const html = printRef.current?.innerHTML;
    if (!html) return;
    setPrinting(true);
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    document.body.appendChild(frame);
    const doc = frame.contentDocument;
    const win = frame.contentWindow;
    if (!doc || !win) {
      frame.remove();
      setPrinting(false);
      return;
    }
    doc.open();
    doc.write(
      `<!doctype html><html><head><meta charset="utf-8"><title>QR labels</title><style>` +
        `@page { size: ${paper.width}mm ${paper.height}mm; margin: 0; } html, body { margin: 0; padding: 0; background: #fff; }` +
        labelCss +
        `</style></head><body>${html}</body></html>`
    );
    doc.close();
    const cleanup = () => {
      setPrinting(false);
      setTimeout(() => frame.remove(), 500);
    };
    win.addEventListener('afterprint', cleanup, { once: true });
    setTimeout(() => {
      win.focus();
      win.print();
      // Some browsers do not send afterprint from a frame.
      setTimeout(cleanup, 1500);
    }, 150);
  };

  const previewScale = 0.4;
  const field = (text: string) => <div className="qr-form__label">{text}</div>;

  return (
    <>
      <FormModal
        open={open}
        icon={<QrcodeOutlined />}
        title={title}
        width={820}
        onCancel={onClose}
        destroyOnHidden
        footer={
          <>
            <Button onClick={onClose} style={{ minWidth: 96 }}>
              Close
            </Button>
            <Button
              type="primary"
              icon={<PrinterOutlined />}
              disabled={!items.length}
              loading={printing}
              onClick={print}
              style={{ fontWeight: 700 }}
            >
              Print {labelCount} label{labelCount === 1 ? '' : 's'}
            </Button>
          </>
        }
      >
        <Row gutter={[24, 16]} className="qr-form">
          <Col xs={24} md={11}>
            {field('Paper')}
            <Select
              value={paperId}
              onChange={(v) => setPaperId(v as PaperId)}
              style={{ width: '100%', marginBottom: 14 }}
              options={Object.values(PAPERS).map((p) => ({
                value: p.id,
                label: `${p.label} (${p.width} × ${p.height} mm)`,
              }))}
            />
            {field('Label size')}
            <Select
              value={sizeId}
              onChange={(v) => setSizeId(v as LabelSizeId)}
              style={{ width: '100%' }}
              options={Object.values(LABEL_SIZES).map((s) => ({ value: s.id, label: s.label, hint: sizeHint(s, paper) }))}
              optionRender={(o) => (
                <div>
                  <div style={{ fontWeight: 600 }}>{o.label}</div>
                  <div style={{ fontSize: 12, color: '#64748b' }}>{o.data.hint}</div>
                </div>
              )}
            />
            <div className="qr-form__hint">{sizeHint(LABEL_SIZES[sizeId], paper)}</div>

            <Row gutter={12} style={{ marginTop: 14 }}>
              <Col span={12}>
                {field('Copies of each')}
                <InputNumber
                  min={1}
                  max={50}
                  precision={0}
                  value={copies}
                  onChange={(v) => setCopies(Math.max(1, Number(v) || 1))}
                  style={{ width: '100%' }}
                />
              </Col>
              <Col span={12}>
                <div className="qr-form__label">
                  Start at label{' '}
                  <Tooltip title="To reuse a partly used sheet. Counted left to right, top to bottom.">
                    <InfoCircleOutlined style={{ color: '#94a3b8', fontSize: 12 }} />
                  </Tooltip>
                </div>
                <InputNumber
                  min={1}
                  max={per}
                  precision={0}
                  value={startAt}
                  onChange={(v) => setStartAt(Math.min(per, Math.max(1, Number(v) || 1)))}
                  style={{ width: '100%' }}
                />
              </Col>
            </Row>

            <Checkbox checked={cutLines} onChange={(e) => setCutLines(e.target.checked)} style={{ marginTop: 14 }}>
              Cut lines (plain paper){' '}
              <Tooltip title="Prints a dotted line around each label, to cut along with scissors. Leave off for sticker sheets, which are already cut.">
                <InfoCircleOutlined style={{ color: '#94a3b8', fontSize: 12 }} />
              </Tooltip>
            </Checkbox>

            <div className="qr-form__summary">
              {labelCount} label{labelCount === 1 ? '' : 's'} · {sheets.length} {paper.label} sheet
              {sheets.length === 1 ? '' : 's'}
              <div className="qr-form__hint">Print at 100% scale with headers and footers off.</div>
            </div>
          </Col>
          <Col xs={24} md={13}>
            {sheets.length ? (
              <SheetPreview sheets={sheets} size={size} paper={paper} scale={previewScale} />
            ) : null}
          </Col>
        </Row>
      </FormModal>
      {open ? (
        <div ref={printRef} style={{ display: 'none' }} aria-hidden>
          {sheets.map((cells, i) => (
            <Sheet key={i} cells={cells} size={size} paper={paper} cutLines={cutLines} />
          ))}
        </div>
      ) : null}
    </>
  );
}
