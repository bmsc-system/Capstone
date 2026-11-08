import apiClient from './client';
import type {
  BreakdownAction,
  BreakdownResolution,
  BreakdownWork,
  MachineDowntimeRecord,
  MachineUnitInfo,
  MachineUnitStatus,
  Operation,
  OperationPauseReason,
  ReworkReasonCategory,
  WorkerJobMaterials,
} from '../types';
import type { DowntimeCategory } from '../constants/downtimeReasons';

export const operationsApi = {
  mine: () => apiClient.get<Operation[]>('/operations/mine'),
  /** Raw materials of the worker's open jobs (no costs). */
  myMaterials: () => apiClient.get<WorkerJobMaterials[]>('/operations/mine/materials'),
  start: (id: string, timestamp?: string) =>
    apiClient.post<Operation>(`/operations/${id}/start`, { timestamp }),
  pause: (id: string, reason: OperationPauseReason, note?: string, timestamp?: string) =>
    apiClient.post<Operation>(`/operations/${id}/pause`, { reason, note, timestamp }),
  resume: (id: string, timestamp?: string) =>
    apiClient.post<Operation>(`/operations/${id}/resume`, { timestamp }),
  complete: (id: string, timestamp?: string) =>
    apiClient.post<Operation>(`/operations/${id}/complete`, { timestamp }),
  rework: (
    id: string,
    data: { category: ReworkReasonCategory; reason?: string; note?: string }
  ) =>
    apiClient.post<Operation>(`/operations/${id}/rework`, {
      category: data.category,
      reason: data.reason ?? data.note,
    }),
  assign: (id: string, assignedWorkerId: string, helperIds?: string[]) =>
    apiClient.patch<Operation>(`/operations/${id}/assign`, {
      assignedWorkerId,
      ...(helperIds ? { helperIds } : {}),
    }),
  sendOut: (id: string, sentOutDate: string, sentTo: string) =>
    apiClient.post<Operation>(`/operations/${id}/send-out`, { sentOutDate, sentTo }),
  markReturned: (id: string, returnedDate: string) =>
    apiClient.post<Operation>(`/operations/${id}/return`, { returnedDate }),
  machineUnitStatus: (includeInactive = false) =>
    apiClient.get<MachineUnitStatus[]>('/operations/machine-units/status', {
      params: includeInactive ? { includeInactive: true } : undefined,
    }),
  createMachineUnit: (machineTypeId: string, label?: string) =>
    apiClient.post<MachineUnitInfo>('/operations/machine-units', {
      machineTypeId,
      label: label || undefined,
    }),
  setMachineUnitActive: (unitId: string, active: boolean) =>
    apiClient.patch<MachineUnitStatus>(`/operations/machine-units/${unitId}`, { active }),
  setMachineUnitDefaultOperator: (unitId: string, defaultOperatorId: string | null) =>
    apiClient.patch<MachineUnitInfo>(`/operations/machine-units/${unitId}`, {
      defaultOperatorId,
    }),
  openDowntime: (
    unitId: string,
    category: DowntimeCategory,
    note?: string,
    link?: { operationId?: string; jobOrderId?: string },
    expectedRepairDate?: string | null
  ) =>
    apiClient.post<MachineDowntimeRecord>(`/operations/machine-units/${unitId}/downtime`, {
      category,
      note,
      ...link,
      ...(expectedRepairDate ? { expectedRepairDate } : {}),
    }),
  setExpectedRepairDate: (downtimeId: string, expectedRepairDate: string | null) =>
    apiClient.patch<MachineDowntimeRecord>(`/operations/machine-units/downtime/${downtimeId}`, {
      expectedRepairDate,
    }),
  closeDowntime: (downtimeId: string, note?: string) =>
    apiClient.post<MachineDowntimeRecord>(`/operations/machine-units/downtime/${downtimeId}/close`, {
      note,
    }),
  machineUnitDowntime: (unitId: string) =>
    apiClient.get<MachineDowntimeRecord[]>(`/operations/machine-units/${unitId}/downtime`),
  breakdownWork: (unitId: string) =>
    apiClient.get<BreakdownWork>(`/operations/machine-units/${unitId}/downtime/work`),
  resolveBreakdownWork: (unitId: string, action: BreakdownAction) =>
    apiClient.post<BreakdownResolution>(`/operations/machine-units/${unitId}/downtime/work`, {
      action,
    }),
};
