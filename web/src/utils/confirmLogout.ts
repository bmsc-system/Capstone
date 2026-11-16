import { LogoutOutlined } from '@ant-design/icons';
import { createElement } from 'react';
import { confirmAction } from './confirmAction';

/** Log-out confirm: the same window as every other confirmation. A second
 *  press while it is open does not stack another copy (same title). */
export function confirmLogout(onOk: () => void, content?: string) {
  confirmAction({
    title: 'Log out?',
    content: content || 'You will need to sign in again.',
    okText: 'Log out',
    cancelText: 'Stay signed in',
    danger: true,
    icon: createElement(LogoutOutlined),
    onOk: () => {
      onOk();
    },
  });
}
