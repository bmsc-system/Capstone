import apiClient from './client';
import type { NotificationLog } from '../types';

export const notificationsApi = {
  list: (params?: {
    jobOrderId?: string;
    clientId?: string;
    status?: string;
    limit?: number;
    /** One or more events, comma-separated (PASSWORD_RESET, INVITATION, or a milestone). */
    event?: string;
  }) =>
    apiClient.get<NotificationLog[]>('/notifications', { params }),
};
