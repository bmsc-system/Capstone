import apiClient from './client';
import type {
  ConsumableIssue,
  ConsumableIssueOptions,
  ConsumableIssueWorker,
  ConsumablePurchase,
  ConsumableReport,
  WorkerConsumables,
} from '../types';

export interface RecordPurchaseInput {
  toolId: string;
  purchasedOn: string;
  quantity: number;
  unitCost?: number | null;
  boughtFrom?: string | null;
  note?: string | null;
}

export interface IssueConsumableInput {
  toolId: string;
  workerId: string;
  quantity: number;
  /** null: not for a particular operation. */
  operationId: string | null;
}

/** The Office Staff keep the consumables: they record what is bought and hand
    items to workers for their operations; stock on hand follows both. */
export const consumablesApi = {
  purchases: (toolId?: string) =>
    apiClient.get<ConsumablePurchase[]>('/consumables/purchases', { params: toolId ? { toolId } : undefined }),
  record: (data: RecordPurchaseInput) => apiClient.post<ConsumablePurchase>('/consumables/purchases', data),
  remove: (id: string) => apiClient.delete(`/consumables/purchases/${id}`),
  /** Older running-low reports still open (Admin, Office Staff). */
  reports: () => apiClient.get<ConsumableReport[]>('/consumables/requests'),
  dismissReport: (id: string) => apiClient.post<ConsumableReport>(`/consumables/requests/${id}/dismiss`),
  /** Hand-outs, newest first. */
  issues: (params?: { toolId?: string; workerId?: string; jobOrderId?: string; limit?: number }) =>
    apiClient.get<ConsumableIssue[]>('/consumables/issues', { params }),
  /** Office Staff: hand a consumable to a worker. */
  issue: (data: IssueConsumableInput) => apiClient.post<ConsumableIssue>('/consumables/issues', data),
  undoIssue: (id: string) => apiClient.post<ConsumableIssue>(`/consumables/issues/${id}/undo`),
  issueWorkers: () => apiClient.get<ConsumableIssueWorker[]>('/consumables/issue-workers'),
  issueOptions: (workerId: string) =>
    apiClient.get<ConsumableIssueOptions>('/consumables/issue-options', { params: { workerId } }),
  /** Production Worker: what is available and what was handed to them (no prices). */
  forWorker: () => apiClient.get<WorkerConsumables>('/consumables/for-worker'),
  /** Consumables handed out for a job's operations. */
  jobIssues: (jobId: string) => apiClient.get<ConsumableIssue[]>(`/consumables/job/${jobId}/issues`),
};
