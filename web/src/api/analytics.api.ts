import apiClient from './client';
import type {
  OfficeSummary,
  AnalyticsByMachine,
  AnalyticsByOperationType,
  AnalyticsByWorker,
  AnalyticsDelays,
  AnalyticsDemandCapacity,
  AnalyticsDemandForecast,
  AnalyticsJobOrders,
  AnalyticsOverview,
  AnalyticsInventory,
  AnalyticsPurchasing,
  AnalyticsSalesForecast,
  AnalyticsSalesSummary,
  AnalyticsTrend,
  AnalyticsUtilizationWeekly,
  CompletionEstimates,
  ConsumableReplenishment,
  MyWorkSummary,
} from '../types';

export type AnalyticsDateParams = {
  from?: string;
  to?: string;
  minOps?: number;
};

export const analyticsApi = {
  overview: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsOverview>('/analytics/overview', { params }),
  byWorker: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsByWorker>('/analytics/efficiency/by-worker', { params }),
  byOperationType: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsByOperationType>('/analytics/efficiency/by-operation-type', {
      params,
    }),
  byMachine: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsByMachine>('/analytics/efficiency/by-machine', { params }),
  trend: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsTrend>('/analytics/efficiency/trend', { params }),
  delays: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsDelays>('/analytics/delays', { params }),
  salesSummary: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsSalesSummary>('/analytics/sales/summary', { params }),
  salesForecast: (params?: AnalyticsDateParams & { months?: number }) =>
    apiClient.get<AnalyticsSalesForecast>('/analytics/sales/forecast', { params }),
  demandForecast: (params?: { months?: number }) =>
    apiClient.get<AnalyticsDemandForecast>('/analytics/demand/forecast', { params }),
  consumableReplenishment: () =>
    apiClient.get<ConsumableReplenishment>('/analytics/consumables/replenishment'),
  demandCapacity: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsDemandCapacity>('/analytics/demand/capacity', { params }),
  inventory: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsInventory>('/analytics/inventory', { params }),
  purchasing: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsPurchasing>('/analytics/purchasing', { params }),
  jobOrders: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsJobOrders>('/analytics/job-orders', { params }),
  utilizationWeekly: (params?: AnalyticsDateParams) =>
    apiClient.get<AnalyticsUtilizationWeekly>('/analytics/utilization/weekly', { params }),
  completionEstimates: () => apiClient.get<CompletionEstimates>('/analytics/completion-estimates'),
  mySummary: () => apiClient.get<MyWorkSummary>('/analytics/me'),
  officeSummary: () => apiClient.get<OfficeSummary>('/analytics/office-summary'),
};
