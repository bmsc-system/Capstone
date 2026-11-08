import apiClient from './client';
import type {
  OperationType,
  SkillEvidence,
  User,
  WorkerSchedule,
  WorkerSkill,
  WorkerWorkHistory,
} from '../types';

/** Outcome of sending an invitation. Never contains the link or code. */
export interface InviteDelivery {
  status: 'SENT' | 'FAILED' | 'SKIPPED' | 'NOT_SENT' | 'PENDING';
  channel: 'EMAIL' | string | null;
  recipient: string | null;
  errorMessage: string | null;
}

export type InviteResponse = {
  delivery?: InviteDelivery;
  setupUrl?: string | null;
};

export const usersApi = {
  list: () => apiClient.get<User[]>('/users'),
  get: (id: string) => apiClient.get<User>(`/users/${id}`),
  create: (data: {
    email: string;
    fullName: string;
    nickname?: string;
    role: string;
    mobileNumber?: string;
  }) => apiClient.post<User & InviteResponse>('/users', data),
  /** Changing a pending user's email also sends a fresh setup link (delivery, setupUrl). */
  update: (id: string, data: Partial<User>) =>
    apiClient.patch<User & InviteResponse>(`/users/${id}`, data),
  uploadPhoto: (id: string, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return apiClient.post<User>(`/users/${id}/photo`, body, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  deletePhoto: (id: string) => apiClient.delete<User>(`/users/${id}/photo`),
  getPhoto: (id: string) => apiClient.get<Blob>(`/users/${id}/photo`, { responseType: 'blob' }),
  deactivate: (id: string) => apiClient.delete(`/users/${id}`),
  /** Emails a new setup link; deliver: false only returns it (copy-link fallback). */
  resendInvite: (id: string, opts?: { deliver?: boolean }) =>
    apiClient.post<InviteResponse>(`/users/${id}/invite`, opts ?? {}),
  revokeInvite: (id: string) => apiClient.delete(`/users/${id}/invite`),
  revokeDevices: (id: string) => apiClient.delete(`/users/${id}/devices`),
};

export const workerProfileApi = {
  getSkills: (workerId: string) =>
    apiClient.get<WorkerSkill[]>(`/workers/${workerId}/skills`),
  putSkills: (workerId: string, skills: Omit<WorkerSkill, 'id' | 'workerId'>[]) =>
    apiClient.put<WorkerSkill[]>(`/workers/${workerId}/skills`, { skills }),
  getSkillEvidence: (workerId: string) =>
    apiClient.get<SkillEvidence>(`/workers/${workerId}/skill-evidence`),
  getSchedule: (workerId: string) =>
    apiClient.get<WorkerSchedule[]>(`/workers/${workerId}/schedule`),
  putSchedule: (workerId: string, schedule: Omit<WorkerSchedule, 'id' | 'workerId'>[]) =>
    apiClient.put<WorkerSchedule[]>(`/workers/${workerId}/schedule`, { schedule }),
  getDetail: (workerId: string) =>
    apiClient.get<User>(`/workers/${workerId}`),
  getHistory: (
    workerId: string,
    params?: { from?: string; to?: string; page?: number; perPage?: number }
  ) => apiClient.get<WorkerWorkHistory>(`/workers/${workerId}/history`, { params }),
};

export const operationTypesApi = {
  list: (activeOnly = true) =>
    apiClient.get<OperationType[]>('/operation-types', {
      params: activeOnly ? {} : { active: 'false' },
    }),
};
