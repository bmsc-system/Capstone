import apiClient from './client';
import type { StaffAlert } from '../types';

export type AlertPage = { items: StaffAlert[]; unreadCount: number; total: number; hasMore: boolean };

export const alertsApi = {
  list: (params?: { limit?: number; offset?: number; unread?: boolean }) =>
    apiClient.get<AlertPage>('/alerts', {
      params: params
        ? { limit: params.limit, offset: params.offset, unread: params.unread ? 1 : undefined }
        : undefined,
    }),
  unreadCount: () => apiClient.get<{ unreadCount: number }>('/alerts/unread-count'),
  markRead: (id: string) => apiClient.post<StaffAlert>(`/alerts/${id}/read`),
  markAllRead: () => apiClient.post<{ marked: number }>('/alerts/read-all'),
};
