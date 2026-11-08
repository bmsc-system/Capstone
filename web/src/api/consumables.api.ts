import apiClient from './client';
import type { ConsumablePurchase, ConsumableReport, ConsumableReportLevel, WorkerConsumable } from '../types';

export interface RecordPurchaseInput {
  toolId: string;
  purchasedOn: string;
  quantity: number;
  unitCost?: number | null;
  boughtFrom?: string | null;
  note?: string | null;
}

/** Consumables are not counted; the shop records what it buys and the forecast reads it. */
export const consumablesApi = {
  purchases: (toolId?: string) =>
    apiClient.get<ConsumablePurchase[]>('/consumables/purchases', { params: toolId ? { toolId } : undefined }),
  record: (data: RecordPurchaseInput) => apiClient.post<ConsumablePurchase>('/consumables/purchases', data),
  remove: (id: string) => apiClient.delete(`/consumables/purchases/${id}`),
  /** Open running-low reports from the shop floor (Admin, Office Staff). */
  reports: () => apiClient.get<ConsumableReport[]>('/consumables/requests'),
  dismissReport: (id: string) => apiClient.post<ConsumableReport>(`/consumables/requests/${id}/dismiss`),
  /** Production Worker: the consumables list (no prices) and running-low reports. */
  forWorker: () => apiClient.get<WorkerConsumable[]>('/consumables/for-worker'),
  report: (toolId: string, level: ConsumableReportLevel) =>
    apiClient.post<ConsumableReport>(`/consumables/${toolId}/report`, { level }),
  withdrawReport: (id: string) => apiClient.post<ConsumableReport>(`/consumables/requests/${id}/withdraw`),
};
