import apiClient from './client';
import type {
  AttendanceDaySheet,
  AttendanceHistory,
  AttendanceRecord,
  MarkAwayResult,
  WorkerAbsence,
} from '../types';

export const attendanceApi = {
  day: (date: string) => apiClient.get<AttendanceDaySheet>('/attendance/day', { params: { date } }),
  history: (workerId: string, params: { from: string; to: string }) =>
    apiClient.get<AttendanceHistory>(`/attendance/workers/${workerId}`, { params }),
  clockIn: (data: { workerId: string; clockIn: string; note?: string }) =>
    apiClient.post<AttendanceRecord>('/attendance/clock-in', data),
  clockOut: (id: string, data: { clockOut: string }) =>
    apiClient.post<AttendanceRecord>(`/attendance/${id}/clock-out`, data),
  update: (id: string, data: { clockIn?: string; clockOut?: string | null; note?: string | null }) =>
    apiClient.patch<AttendanceRecord>(`/attendance/${id}`, data),
  remove: (id: string) => apiClient.delete(`/attendance/${id}`),
  /** Away periods covering the day, and coming ones. */
  absences: (date?: string) =>
    apiClient.get<WorkerAbsence[]>('/attendance/absences', { params: date ? { date } : undefined }),
  /** Mark a worker away; their work in the period is handed to others or moved. */
  markAway: (data: {
    workerId: string;
    fromDate: string;
    toDate: string;
    reason: 'ABSENT' | 'EMERGENCY' | 'LEAVE';
    note?: string;
  }) => apiClient.post<MarkAwayResult>('/attendance/absences', data),
  /** The worker is back early. */
  cancelAbsence: (id: string) => apiClient.post<WorkerAbsence>(`/attendance/absences/${id}/cancel`),
  dayCsv: (date: string) =>
    apiClient.get<string>('/attendance/day.csv', { params: { date }, responseType: 'text' }),
  historyCsv: (workerId: string, params: { from: string; to: string }) =>
    apiClient.get<string>(`/attendance/workers/${workerId}.csv`, {
      params,
      responseType: 'text',
    }),
};
