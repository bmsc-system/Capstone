import type { ReactNode } from 'react';
import './confirmAction.css';

/** Fields inside a confirm box, 16px apart, under an optional explanation. */
export function DialogFields({ intro, children }: { intro?: ReactNode; children: ReactNode }) {
  return (
    <>
      {intro ? <div className="app-dialog-intro">{intro}</div> : null}
      <div className={`app-dialog-fields${intro ? ' app-dialog-fields--after-intro' : ''}`}>{children}</div>
    </>
  );
}

/** One field in a confirm box: a dark label, the control, and help text under it. */
export function DialogField({
  label,
  optional,
  help,
  children,
}: {
  label: ReactNode;
  optional?: boolean;
  help?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app-dialog-field">
      <div className="app-dialog-field__label">
        {label}
        {optional ? <span className="app-dialog-field__optional"> (optional)</span> : null}
      </div>
      {children}
      {help ? <div className="app-dialog-field__help">{help}</div> : null}
    </div>
  );
}
