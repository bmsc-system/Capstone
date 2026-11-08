import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { Button, Modal } from 'antd';
import type { ButtonProps } from 'antd';

export type FormModalSize = 'sm' | 'md' | 'lg';

const WIDTH: Record<FormModalSize, number> = { sm: 480, md: 560, lg: 760 };

type Props = {
  open: boolean;
  title: ReactNode;
  /** One line under the title (what the form is for, or what it applies to). */
  subtitle?: ReactNode;
  icon: ReactNode;
  /** Small forms get a smaller header and tighter padding. */
  size?: FormModalSize;
  width?: number;
  onCancel: () => void;
  /** Default footer: Cancel and a primary button. Pass `footer` to replace it, or null for none. */
  onOk?: () => void;
  okText?: ReactNode;
  cancelText?: ReactNode;
  confirmLoading?: boolean;
  okButtonProps?: ButtonProps;
  footer?: ReactNode | null;
  centered?: boolean;
  destroyOnHidden?: boolean;
  forceRender?: boolean;
  afterClose?: () => void;
  zIndex?: number;
  /** Padding around the body; the default suits a form. */
  bodyStyle?: CSSProperties;
  children?: ReactNode;
};

/**
 * Every form dialog: the navy header with an icon, title and subtitle, square
 * corners, and the action row at the bottom. Matches Add supplier / Add machine.
 */
export default function FormModal({
  open,
  title,
  subtitle,
  icon,
  size = 'md',
  width,
  onCancel,
  onOk,
  okText = 'Save',
  cancelText = 'Cancel',
  confirmLoading,
  okButtonProps,
  footer,
  centered = true,
  destroyOnHidden,
  forceRender,
  afterClose,
  zIndex,
  bodyStyle,
  children,
}: Props) {
  const small = size === 'sm';
  // A click right after the form opens is the rest of a double-click on the
  // button that opened it; it must not press Save on values never looked at.
  const openedAt = useRef(0);
  useEffect(() => {
    if (open) openedAt.current = Date.now();
  }, [open]);
  const pressOk = () => {
    if (Date.now() - openedAt.current < 450 || confirmLoading) return;
    onOk?.();
  };
  const actions =
    footer === null ? null : footer !== undefined ? (
      <div className="app-form-modal__footer">{footer}</div>
    ) : (
      <div className="app-form-modal__footer">
        <Button onClick={onCancel} style={{ minWidth: 96 }}>
          {cancelText}
        </Button>
        <Button
          type="primary"
          loading={confirmLoading}
          onClick={pressOk}
          {...okButtonProps}
          style={{ fontWeight: 700, minWidth: 120, ...okButtonProps?.style }}
        >
          {okText}
        </Button>
      </div>
    );

  return (
    <Modal
      open={open}
      onCancel={onCancel}
      footer={null}
      width={width ?? WIDTH[size]}
      centered={centered}
      destroyOnHidden={destroyOnHidden}
      forceRender={forceRender}
      afterClose={afterClose}
      zIndex={zIndex}
      className={`app-form-modal app-form-modal--${size}`}
      styles={{
        container: { padding: 0, borderRadius: 0, overflow: 'hidden' },
        body: { padding: 0 },
      }}
      closable={false}
    >
      <div className="app-form-modal__head">
        <div className="app-form-modal__icon" aria-hidden>
          {icon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="app-form-modal__title">{title}</div>
          {subtitle ? <div className="app-form-modal__sub">{subtitle}</div> : null}
        </div>
        <button type="button" className="app-form-modal__close" onClick={onCancel} aria-label="Close">
          ×
        </button>
      </div>
      <div
        className="app-form-modal__body"
        style={{ padding: small ? '16px 20px 18px' : '20px 24px 20px', ...bodyStyle }}
      >
        {children}
      </div>
      {actions}
    </Modal>
  );
}
