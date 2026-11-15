/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import type { ReactNode } from 'react';
import ShopDetailsPage from './ShopDetailsPage';
import { PageHeaderActionsProvider } from '../../layouts/pageHeaderActions';

vi.mock('../../api/shopDetails.api', () => ({
  shopDetailsApi: {
    get: () =>
      Promise.resolve({
        data: {
          shopName: 'Brothers Machine',
          tagline: 'Fab work',
          address: 'Malvar',
          telephone: '(043) 1',
          mobileNumbers: '0917',
          email: 'shop@test.local',
          poApproverName: 'Greg',
          poApproverTitle: 'GM',
          joApproverName: 'Gary',
          joApproverTitle: 'Prod',
          updatedAt: '2026-10-06T03:07:00Z',
        },
      }),
    update: (data: unknown) => Promise.resolve({ data }),
  },
}));

vi.mock('../../api/notifications.api', () => ({
  notificationsApi: {
    list: () => Promise.resolve({ data: [] }),
  },
}));

vi.mock('../../hooks/useShopDetails', () => ({
  invalidateShopDetails: () => undefined,
}));

function renderPage(ui: ReactNode) {
  return render(
    <ConfigProvider>
      <MemoryRouter>
        <PageHeaderActionsProvider>{ui}</PageHeaderActionsProvider>
      </MemoryRouter>
    </ConfigProvider>
  );
}

describe('ShopDetailsPage', () => {
  it('renders without throwing (no useBlocker / blank crash)', async () => {
    await act(async () => {
      renderPage(<ShopDetailsPage />);
    });

    await waitFor(() => {
      expect(screen.getByDisplayValue('Brothers Machine')).toBeTruthy();
    });
    expect(screen.getByRole('button', { name: /save/i })).toBeTruthy();
    expect(screen.getByText('Letterhead')).toBeTruthy();
    expect(screen.getByText('Contact')).toBeTruthy();
    expect(screen.getByText('Approvers')).toBeTruthy();
  });

  it('updates letterhead preview when the shop name changes', async () => {
    await act(async () => {
      renderPage(<ShopDetailsPage />);
    });
    await waitFor(() => expect(screen.getByDisplayValue('Brothers Machine')).toBeTruthy());

    const nameInput = screen.getByLabelText(/shop name/i);
    await act(async () => {
      fireEvent.change(nameInput, { target: { value: 'New Shop Name' } });
    });

    // jsdom: Grid breakpoints are unset → narrow layout → preview opens via button/drawer.
    // Wide layout would already show the live sheet.
    const previewBtn = screen.queryByRole('button', { name: /preview letterhead/i });
    if (previewBtn) {
      await act(async () => {
        fireEvent.click(previewBtn);
      });
    }

    await waitFor(() => {
      expect(screen.getByTestId('letterhead-preview-sheet')).toBeTruthy();
      expect(document.querySelector('.shop-print-name')?.textContent).toContain('New Shop Name');
    });
  });
});
