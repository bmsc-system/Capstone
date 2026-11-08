import { LogoutOutlined } from '@ant-design/icons';
import { createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';

/**
 * Custom logout confirm — plain buttons sharing `.acct-sheet-btn` with
 * "Account security" so Ant Design cannot override typography.
 */
/** Taps in the first moments after the card opens are the tail end of a
 *  double-click on the Log out button, not a choice. Same delay as confirmAction. */
const ARM_DELAY_MS = 450;
let isOpen = false;

export function confirmLogout(onOk: () => void, content?: string) {
  // A second press while the card is open must not stack another copy.
  if (isOpen) return;
  isOpen = true;
  let armed = false;
  const armTimer = window.setTimeout(() => {
    armed = true;
  }, ARM_DELAY_MS);

  const host = document.createElement('div');
  host.className = 'logout-confirm-host';
  document.body.appendChild(host);

  let root: Root | null = createRoot(host);

  const close = () => {
    window.clearTimeout(armTimer);
    root?.unmount();
    root = null;
    host.remove();
    isOpen = false;
    document.removeEventListener('keydown', onKey);
  };

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') close();
  };
  document.addEventListener('keydown', onKey);

  root.render(
    createElement(
      'div',
      {
        className: 'logout-confirm-overlay',
        role: 'presentation',
      },
      createElement(
        'div',
        {
          className: 'logout-confirm-card',
          role: 'dialog',
          'aria-modal': true,
          'aria-labelledby': 'logout-confirm-title',
        },
        createElement(
          'div',
          { id: 'logout-confirm-title', className: 'logout-confirm-card__title' },
          'Log out?',
        ),
        createElement(
          'div',
          { className: 'logout-confirm-card__body' },
          content || 'You will need to sign in again.',
        ),
        createElement(
          'div',
          { className: 'logout-confirm-card__actions' },
          createElement(
            'button',
            {
              type: 'button',
              className: 'acct-sheet-btn',
              onClick: () => {
                if (armed) close();
              },
            },
            'Stay signed in',
          ),
          createElement(
            'button',
            {
              type: 'button',
              className: 'acct-sheet-btn acct-sheet-btn--danger',
              onClick: () => {
                if (!armed) return;
                close();
                onOk();
              },
            },
            createElement(LogoutOutlined),
            'Log out',
          ),
        ),
      ),
    ),
  );
}
