import { useEffect, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Button, ConfigProvider, Modal, type ThemeConfig } from 'antd';
import { ExclamationCircleOutlined, InfoCircleOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { officeTheme, officeTouchTheme, workerTheme } from '../theme/antTheme';
import { TOUCH_QUERY } from '../hooks/useViewport';
import './confirmAction.css';

type ConfirmOptions = {
  title: ReactNode;
  content?: ReactNode;
  okText?: string;
  cancelText?: string;
  /** Maroon icon and button for deleting, removing, disabling or signing out. */
  danger?: boolean;
  /** Wider box for content with fields, such as a date or a QR code. */
  wide?: boolean;
  /** Icon in the round badge beside the title; a question mark (or ! for danger) by default. */
  icon?: ReactNode;
  /** A rejected promise keeps the dialog open so the person can try again. */
  onOk?: () => unknown | Promise<unknown>;
  onCancel?: () => void;
};

type NoticeOptions = {
  title: ReactNode;
  content?: ReactNode;
  okText?: string;
  wide?: boolean;
  icon?: ReactNode;
};

type DialogProps = ConfirmOptions & { single?: boolean; close: () => void };

/** Taps in the first moments after the dialog opens are the tail end of a
 *  double-click on the button that opened it, not a choice. */
const ARM_DELAY_MS = 450;

/** The dialog mounts outside the app's tree, so it takes the same theme the
 *  screen behind it uses: worker app, office on a phone, or office. */
function pickTheme(): ThemeConfig {
  if (typeof document !== 'undefined' && document.querySelector('.worker-shell')) return workerTheme;
  return typeof window !== 'undefined' && window.matchMedia?.(TOUCH_QUERY).matches ? officeTouchTheme : officeTheme;
}

function Dialog({ title, content, okText, cancelText, danger, wide, icon, onOk, onCancel, single, close }: DialogProps) {
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setArmed(true), ARM_DELAY_MS);
    return () => window.clearTimeout(t);
  }, []);

  const cancel = () => {
    if (busy || !open) return;
    setOpen(false);
    onCancel?.();
  };

  const confirm = async () => {
    if (!armed || busy || !open) return;
    if (!onOk) {
      setOpen(false);
      return;
    }
    setBusy(true);
    try {
      await onOk();
      setOpen(false);
    } catch {
      setBusy(false);
    }
  };

  const badge = icon ?? (danger ? <ExclamationCircleOutlined /> : single ? <InfoCircleOutlined /> : <QuestionCircleOutlined />);

  return (
    <Modal
      open={open}
      afterClose={close}
      centered
      width={wide ? 480 : 440}
      closable={false}
      keyboard={!busy}
      mask={{ closable: armed && !busy }}
      onCancel={cancel}
      className={`app-dialog${single ? ' app-dialog--single' : ''}`}
      title={
        <span className="app-dialog__head">
          <span className={`app-dialog__badge${danger ? ' app-dialog__badge--danger' : ''}`} aria-hidden="true">
            {badge}
          </span>
          <span className="app-dialog__title">{title}</span>
        </span>
      }
      footer={
        single ? (
          <Button type="primary" onClick={() => armed && setOpen(false)}>
            {okText || 'OK'}
          </Button>
        ) : (
          <>
            <Button onClick={() => armed && cancel()} disabled={busy}>
              {cancelText || 'Cancel'}
            </Button>
            <Button type="primary" danger={danger} loading={busy} onClick={confirm}>
              {okText || 'OK'}
            </Button>
          </>
        )
      }
    >
      {content}
    </Modal>
  );
}

/** Pressing the same button again while its dialog is open must not stack a
 *  second copy that would run the action twice. A different dialog (such as a
 *  notice shown by the confirmed action) still opens. */
const openTitles = new Set<string>();

function mount(props: Omit<DialogProps, 'close'>) {
  const key = typeof props.title === 'string' ? `${props.single ? 'n' : 'c'}:${props.title}` : null;
  if (key) {
    if (openTitles.has(key)) return;
    openTitles.add(key);
  }
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  const close = () => {
    if (key) openTitles.delete(key);
    // Called after the closing animation; unmount outside React's render.
    window.setTimeout(() => {
      root.unmount();
      host.remove();
    });
  };
  root.render(
    <ConfigProvider theme={pickTheme()}>
      <Dialog {...props} close={close} />
    </ConfigProvider>
  );
}

/** Ask before an action, in the app's own window. */
export function confirmAction(options: ConfirmOptions) {
  mount(options);
}

/** A message with one button. */
export function showNotice(options: NoticeOptions) {
  mount({ ...options, single: true });
}
