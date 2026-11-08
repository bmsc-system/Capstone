import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, message } from 'antd';
import {
  CheckCircleFilled,
  CameraOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  QrcodeOutlined,
  StopOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import type { Html5Qrcode } from 'html5-qrcode';
import { toolsApi } from '../../api/tools.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { WorkerPageHeader, useWorkerTheme } from '../../layouts/WorkerLayout';
import { useOfflineOptional } from '../../offline/OfflineProvider';
import { announceToolsChanged } from '../worker/toolsEvents';
import type { ToolEvent, ToolUnit } from '../../types';
import { confirmAction } from '../../utils/confirmAction';

type CameraState = 'starting' | 'scanning' | 'denied' | 'unavailable';
type ScanIntent = 'BORROW' | 'RETURN';

const IS_IOS = typeof navigator !== 'undefined' && /iPhone|iPad|iPod/i.test(navigator.userAgent);

/** How to turn the camera back on, for the browser this phone is likely using. */
function AllowCameraSteps() {
  return IS_IOS ? (
    <ol className="w-scan__steps">
      <li>
        Tap <b>aA</b> at the left of the address bar.
      </li>
      <li>
        Tap <b>Website Settings</b>, then set <b>Camera</b> to <b>Allow</b>.
      </li>
      <li>
        Come back and tap <b>Try again</b>.
      </li>
    </ol>
  ) : (
    <ol className="w-scan__steps">
      <li>
        Tap the <b>lock</b> or <b>settings</b> icon next to the web address.
      </li>
      <li>
        Tap <b>Permissions</b> (or <b>Site settings</b>) and set <b>Camera</b> to <b>Allow</b>.
      </li>
      <li>
        Come back and tap <b>Try again</b>.
      </li>
    </ol>
  );
}

function undoStillOpen(canUndoUntil?: string | null) {
  if (!canUndoUntil) return false;
  const until = Date.parse(canUndoUntil);
  return Number.isFinite(until) && until > Date.now();
}

export default function ScanToolPage() {
  const { colors } = useWorkerTheme();
  const { user } = useAuth();
  const offline = useOfflineOptional();
  const scannerRef = useRef<Html5Qrcode | null>(null);
  const [cameraState, setCameraState] = useState<CameraState>('starting');
  const [detectedCode, setDetectedCode] = useState('');
  const [detectedUnit, setDetectedUnit] = useState<ToolUnit | null>(null);
  const [detectedIntent, setDetectedIntent] = useState<ScanIntent | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [result, setResult] = useState<ToolEvent | null>(null);
  const [manualCode, setManualCode] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const lookupSeq = useRef(0);
  const detectedCodeRef = useRef('');
  const submittingRef = useRef(false);
  const offlineRef = useRef(offline);
  offlineRef.current = offline;

  const clearDetected = useCallback(() => {
    detectedCodeRef.current = '';
    setDetectedCode('');
    setDetectedUnit(null);
    setDetectedIntent(null);
    setLookupError(null);
    setLookingUp(false);
  }, []);

  const resolveIntent = useCallback(
    (unit: ToolUnit): { intent: ScanIntent | null; error: string | null } => {
      if (unit.status === 'RETIRED') {
        return { intent: null, error: 'This tool is retired' };
      }
      if (unit.status === 'UNDER_REPAIR') {
        return { intent: null, error: 'This tool is under repair' };
      }
      if (unit.status === 'OUT') {
        if (unit.currentHolderId && user?.id && unit.currentHolderId !== user.id) {
          return {
            intent: null,
            error: `Already out with ${unit.currentHolderName || 'another worker'}`,
          };
        }
        return { intent: 'RETURN', error: null };
      }
      if (unit.status === 'AVAILABLE') {
        return { intent: 'BORROW', error: null };
      }
      return { intent: null, error: 'Cannot scan this unit' };
    },
    [user?.id]
  );

  const showScanResult = useCallback((data: ToolEvent) => {
    setResult(data);
    setManualCode('');
    clearDetected();
    announceToolsChanged();
  }, [clearDetected]);

  const performScan = useCallback(
    async (code: string, options?: { intent?: ScanIntent; confirmRepeat?: boolean }) => {
      if (submittingRef.current) return;
      submittingRef.current = true;
      setSubmitting(true);
      try {
        const offlineCtx = offlineRef.current;
        if (offlineCtx && !offlineCtx.online) {
          const outcome = await offlineCtx.record({
            kind: 'consumable_scan',
            scanCode: code.trim(),
            confirmRepeat: Boolean(options?.confirmRepeat),
            label: `Scan ${code.trim()}`,
          });
          if (outcome === 'queued') {
            message.success('Saved on this phone — will send when you are back online');
          } else {
            message.success('Scan recorded');
          }
          clearDetected();
          setManualCode('');
          announceToolsChanged();
          return;
        }

        const { data } = await toolsApi.scan(code.trim(), {
          intent: options?.intent,
          confirmRepeat: options?.confirmRepeat,
        });

        if (data.needsConfirm) {
          confirmAction({
            title: 'Scan again?',
            content: data.confirmMessage || 'You just scanned this item. Scan again?',
            okText: 'Yes, scan again',
            cancelText: 'Cancel',
            onOk: () => performScan(code, { ...options, confirmRepeat: true }),
          });
          return;
        }

        showScanResult(data);
      } catch (err) {
        message.error(getErrorMessage(err));
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
      }
    },
    [clearDetected, showScanResult]
  );

  const lookupUnit = useCallback(
    async (code: string) => {
      const seq = ++lookupSeq.current;
      setLookingUp(true);
      setLookupError(null);
      setDetectedUnit(null);
      setDetectedIntent(null);
      try {
        const { data: unit } = await toolsApi.lookupUnit(code);
        if (seq !== lookupSeq.current || detectedCodeRef.current !== code) return;
        setDetectedUnit(unit);
        const { intent, error } = resolveIntent(unit);
        setDetectedIntent(intent);
        setLookupError(error);
      } catch {
        if (seq !== lookupSeq.current || detectedCodeRef.current !== code) return;
        // Not a tool unit: send it anyway so the server says what it is (consumables are refused).
        setDetectedUnit(null);
        setDetectedIntent(null);
        setLookingUp(false);
        await performScan(code);
        return;
      } finally {
        if (seq === lookupSeq.current) setLookingUp(false);
      }
    },
    [resolveIntent, performScan]
  );

  const onCodeDetected = useCallback(
    (raw: string) => {
      const code = raw.trim();
      if (!code || code === detectedCodeRef.current) return;
      detectedCodeRef.current = code;
      setDetectedCode(code);
      void lookupUnit(code);
    },
    [lookupUnit]
  );

  const handleManual = async () => {
    const code = manualCode.trim();
    if (!code || submitting) return;
    setManualCode('');
    detectedCodeRef.current = code;
    setDetectedCode(code);
    setLookingUp(true);
    setLookupError(null);
    setDetectedUnit(null);
    setDetectedIntent(null);
    try {
      const { data: unit } = await toolsApi.lookupUnit(code);
      setDetectedUnit(unit);
      const { intent, error } = resolveIntent(unit);
      if (!intent) {
        setDetectedIntent(null);
        setLookupError(error);
        if (error) message.error(error);
        return;
      }
      await performScan(code, { intent });
    } catch {
      await performScan(code);
    } finally {
      setLookingUp(false);
    }
  };

  const handleUndo = async () => {
    if (!result?.id || undoing) return;
    setUndoing(true);
    try {
      const { data } = await toolsApi.undoScan(result.id);
      message.success(data.displayLine || 'Scan undone');
      setResult(null);
      announceToolsChanged();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setUndoing(false);
    }
  };

  useEffect(() => {
    if (typeof window !== 'undefined' && (!window.isSecureContext || !navigator.mediaDevices)) {
      setCameraState('unavailable');
      return;
    }
    setCameraState('starting');
    let cancelled = false;
    let qr: Html5Qrcode | null = null;
    let startPromise: Promise<void> = Promise.resolve();

    void import('html5-qrcode').then(({ Html5Qrcode: Html5QrcodeCtor }) => {
      if (cancelled) return;
      qr = new Html5QrcodeCtor('qr-camera-view');
      scannerRef.current = qr;
      startPromise = qr
        .start(
          { facingMode: 'environment' },
          // No qrbox: read the whole camera frame. The video is stretched to
          // fill the screen (object-fit: cover), so a fixed scan box drifts
          // away from the green frame when the camera gives landscape frames.
          { fps: 10 },
          (decodedText) => onCodeDetected(decodedText),
          () => {}
        )
        .then(() => {
          if (!cancelled) setCameraState('scanning');
        })
        .catch(() => {
          if (!cancelled) setCameraState('denied');
        });
    });

    return () => {
      cancelled = true;
      startPromise.finally(() => {
        if (qr?.isScanning) {
          qr.stop()
            .then(() => qr?.clear())
            .catch(() => {});
        }
      });
    };
  }, [onCodeDetected, attempt]);

  const actionLabel = detectedIntent === 'RETURN' ? 'Return' : 'Borrow';
  const canSubmit = Boolean(detectedIntent) && !submitting && !lookingUp;
  const blocked = cameraState === 'denied' || cameraState === 'unavailable';
  const canUndo = Boolean(result?.kind === 'CONSUMABLE_SCAN' && undoStillOpen(result?.canUndoUntil));
  const offlineBanner = offline && !offline.online;

  return (
    <div className="w-scan">
      <WorkerPageHeader
        title="Scan"
        subtitle={offlineBanner ? 'Offline · scans save on this phone' : 'Borrow and return tools'}
      />

      <div className="w-scan__camera">
        <div id="qr-camera-view" />
        {cameraState === 'scanning' && (
          <div className="w-scan__frame" aria-hidden>
            <div className="w-scan__box">
              <span className="w-scan__corner w-scan__corner--tl" />
              <span className="w-scan__corner w-scan__corner--tr" />
              <span className="w-scan__corner w-scan__corner--bl" />
              <span className="w-scan__corner w-scan__corner--br" />
            </div>
          </div>
        )}
        {cameraState === 'starting' && (
          <div className="w-scan__status">
            <span className="w-scan__status-icon">
              <CameraOutlined />
            </span>
            <span>Starting camera…</span>
          </div>
        )}
        {cameraState === 'denied' && (
          <div className="w-scan__status">
            <span className="w-scan__status-icon">
              <StopOutlined />
            </span>
            <div className="w-scan__status-title">Camera is blocked</div>
            <AllowCameraSteps />
            <Button className="w-scan__retry" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </Button>
          </div>
        )}
        {cameraState === 'unavailable' && (
          <div className="w-scan__status">
            <span className="w-scan__status-icon">
              <StopOutlined />
            </span>
            <div className="w-scan__status-title">Camera not available here</div>
            <span style={{ fontSize: 13, maxWidth: 300 }}>
              This page needs a secure (https) connection to use the camera. Type the code below
              instead.
            </span>
          </div>
        )}
      </div>

      <div className="w-scan__sheet">
        <div className="w-scan__grip" aria-hidden />
        {detectedCode ? (
          <div className="w-scan__found">
            <div className="w-scan__code">
              <QrcodeOutlined style={{ color: colors.green }} />
              {detectedCode}
            </div>
            {lookingUp ? (
              <div className="w-note" style={{ marginTop: 4 }}>
                Looking up…
              </div>
            ) : detectedUnit ? (
              <>
                <div className="w-scan__tool">{detectedUnit.toolTypeName}</div>
                <div className="w-note">
                  {detectedUnit.assetCode} ·{' '}
                  {detectedUnit.currentHolderName ? `Held by ${detectedUnit.currentHolderName}` : 'Available'}
                </div>
              </>
            ) : null}
          </div>
        ) : (
          <div className="w-scan__prompt">
            {blocked
              ? 'Type the tool asset code'
              : "Point the camera at a tool's QR code"}
          </div>
        )}
        {lookupError && <div className="w-scan__error">{lookupError}</div>}
        {canSubmit && detectedIntent && (
          <Button
            type="primary"
            block
            className="w-action"
            loading={submitting}
            icon={detectedIntent === 'RETURN' ? <ArrowDownOutlined /> : <ArrowUpOutlined />}
            onClick={() => void performScan(detectedCode, { intent: detectedIntent })}
            style={{
              background: detectedIntent === 'RETURN' ? '#2563eb' : colors.green,
              marginBottom: 10,
              height: 48,
              fontWeight: 700,
            }}
          >
            {actionLabel}
          </Button>
        )}
        {detectedCode && !lookingUp && !canSubmit ? (
          <Button block style={{ height: 48, marginBottom: 10 }} onClick={clearDetected}>
            Scan another
          </Button>
        ) : null}
        <form
          className="w-scan__manual"
          onSubmit={(e) => {
            e.preventDefault();
            void handleManual();
          }}
        >
          <Input
            placeholder={blocked ? 'e.g. SEED-ANGLE-GRINDER-001' : 'Or type the code'}
            value={manualCode}
            onChange={(e) => setManualCode(e.target.value)}
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            aria-label="Scan code"
          />
          <Button
            type={blocked ? 'primary' : 'default'}
            htmlType="submit"
            loading={lookingUp || submitting}
            disabled={!manualCode.trim()}
            style={{ height: 48, minWidth: 72, fontWeight: 700 }}
          >
            Go
          </Button>
        </form>
      </div>

      <Modal open={Boolean(result)} onCancel={() => setResult(null)} footer={null} centered closable={false}>
        <div style={{ textAlign: 'center', padding: '16px 0' }}>
          <CheckCircleFilled style={{ fontSize: 64, color: colors.green, marginBottom: 16 }} />
          {result?.displayLine ? (
            <div style={{ fontSize: 22, fontWeight: 800, marginBottom: 8, lineHeight: 1.3 }}>
              {result.displayLine}
            </div>
          ) : (
            <>
              <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: 2, marginBottom: 4 }}>
                {result?.type === 'RETURN' ? 'RETURNED' : 'BORROWED'}
              </div>
              <div style={{ fontSize: 16, marginBottom: 4 }}>{result?.toolName}</div>
              <div style={{ color: colors.textSecondary, fontSize: 13, marginBottom: 8 }}>
                {result?.assetCode}
              </div>
            </>
          )}
          <div style={{ color: colors.textSecondary, fontSize: 13, marginBottom: 14, lineHeight: 1.45 }}>
            {result?.type === 'RETURN'
              ? 'Returned by mistake? Scan it again to borrow it back.'
              : 'Borrowed by mistake? Scan it again to return it.'}
          </div>
          {canUndo && (
            <Button
              block
              icon={<UndoOutlined />}
              loading={undoing}
              onClick={() => void handleUndo()}
              style={{ height: 48, fontWeight: 700, marginBottom: 10 }}
            >
              Undo
            </Button>
          )}
          <Button
            type="primary"
            block
            size="large"
            style={{ height: 48, fontWeight: 700 }}
            onClick={() => setResult(null)}
          >
            Done — keep scanning
          </Button>
        </div>
      </Modal>
    </div>
  );
}
