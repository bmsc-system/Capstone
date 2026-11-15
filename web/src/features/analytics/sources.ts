import { analyticsApi } from '../../api/analytics.api';
import { suppliersApi } from '../../api/suppliers.api';
import { useAuth } from '../../hooks/useAuth';
import { useAnalyticsPeriod } from './analyticsPeriod';
import { previousPeriod } from './format';
import { useAnalyticsData } from './useAnalyticsData';

type Period = { from: string; to: string };

const key = (name: string, p?: Period) => (p ? `${name}:${p.from}:${p.to}` : name);

export function useIsAdmin() {
  return useAuth().user?.role === 'ADMIN';
}

/** The selected period and the same number of days just before it (for KPI arrows). */
export function usePeriods() {
  const { params } = useAnalyticsPeriod();
  return { period: params, previous: previousPeriod(params) };
}

export const useOverview = (p: Period | null) =>
  useAnalyticsData(p ? key('overview', p) : null, () => analyticsApi.overview(p!));
export const useTrend = (p: Period | null) =>
  useAnalyticsData(p ? key('trend', p) : null, () => analyticsApi.trend(p!));
export const useDelays = (p: Period | null) =>
  useAnalyticsData(p ? key('delays', p) : null, () => analyticsApi.delays(p!));
export const useSalesSummary = (p: Period | null) =>
  useAnalyticsData(p ? key('sales', p) : null, () => analyticsApi.salesSummary(p!));
export const useJobOrders = (p: Period | null) =>
  useAnalyticsData(p ? key('jobs', p) : null, () => analyticsApi.jobOrders(p!));
export const useInventory = (p: Period | null) =>
  useAnalyticsData(p ? key('inventory', p) : null, () => analyticsApi.inventory(p!));
export const usePurchasing = (p: Period | null) =>
  useAnalyticsData(p ? key('purchasing', p) : null, () => analyticsApi.purchasing(p!));
export const useReliability = (p: Period | null) =>
  useAnalyticsData(p ? key('reliability', p) : null, () => suppliersApi.reliability(p!));
export const useUtilization = (p: Period | null) =>
  useAnalyticsData(p ? key('utilization', p) : null, () => analyticsApi.utilizationWeekly(p!));
export const useByWorker = (p: Period | null) =>
  useAnalyticsData(p ? key('byWorker', p) : null, () => analyticsApi.byWorker(p!));
export const useByOperationType = (p: Period | null) =>
  useAnalyticsData(p ? key('byOpType', p) : null, () => analyticsApi.byOperationType(p!));
export const useByMachine = (p: Period | null) =>
  useAnalyticsData(p ? key('byMachine', p) : null, () => analyticsApi.byMachine(p!));

/** Forecasts and stock use all history, not the selected period. ``months`` is how far ahead. */
export const useSalesForecast = (months = 3) =>
  useAnalyticsData(`salesForecast:${months}`, () => analyticsApi.salesForecast({ months }));
export const useDemandForecast = (months = 3) =>
  useAnalyticsData(`demandForecast:${months}`, () => analyticsApi.demandForecast({ months }));
export const useConsumables = () =>
  useAnalyticsData(key('consumables'), () => analyticsApi.consumableReplenishment());
export const useCompletion = (enabled = true) =>
  useAnalyticsData(enabled ? key('completion') : null, () => analyticsApi.completionEstimates());
export const useCapacity = (enabled = true) =>
  useAnalyticsData(enabled ? key('capacity') : null, () => analyticsApi.demandCapacity());
