import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { AuthProvider } from '../hooks/useAuth';
import IdleSessionWatcher from '../auth/IdleSessionWatcher';
import { ProtectedRoute } from './ProtectedRoute';
import RoleAwareLayout from './RoleAwareLayout';
import { NavMemoryProvider } from '../navigation/navMemory';
import LoginPage from '../features/auth/LoginPage';
import SetPasswordPage from '../features/auth/SetPasswordPage';
import ForgotPasswordPage from '../features/auth/ForgotPasswordPage';
import ResetPasswordPage from '../features/auth/ResetPasswordPage';
import PrivacyPolicyPage from '../features/auth/PrivacyPolicyPage';
import { PageSkeleton } from '../components/ui';

const InventoryPage = lazy(() => import('../features/worker/InventoryPage'));
const WorkerHomePage = lazy(() => import('../features/worker/WorkerHomePage'));
const WorkerProfilePage = lazy(() => import('../features/worker/WorkerProfilePage'));
const AccountSecurityPage = lazy(() => import('../features/auth/AccountSecurityPage'));
const JobOrderListPage = lazy(() => import('../features/job-orders/JobOrderListPage'));
const JobOrderFormPage = lazy(() => import('../features/job-orders/JobOrderFormPage'));
const JobOrderPlanningPage = lazy(() => import('../features/job-orders/JobOrderPlanningPage'));
const JobOrderDetailPage = lazy(() => import('../features/job-orders/JobOrderDetailPage'));
const MyAssignmentsPage = lazy(() => import('../features/my-assignments/MyAssignmentsPage'));
const AssignmentDetailPage = lazy(() => import('../features/my-assignments/AssignmentDetailPage'));
const UsersPage = lazy(() => import('../features/users/UsersPage'));
const WorkerSetupPage = lazy(() => import('../features/workers/WorkerSetupPage'));
const AttendancePage = lazy(() => import('../features/attendance/AttendancePage'));
const AuditLogPage = lazy(() => import('../features/audit/AuditLogPage'));
const NotificationsPage = lazy(() => import('../features/notifications/NotificationsPage'));
const ShopDetailsPage = lazy(() => import('../features/settings/ShopDetailsPage'));
const ToolsPage = lazy(() => import('../features/tool-tracking/ToolsPage'));
const ScanToolPage = lazy(() => import('../features/tool-tracking/ScanToolPage'));
const ClientsPage = lazy(() => import('../features/clients/ClientsPage'));
const ClientDetailPage = lazy(() => import('../features/clients/ClientDetailPage'));
const SuppliersAndOrdersPage = lazy(() => import('../features/suppliers/SuppliersAndOrdersPage'));
const SupplierOrderDetailPage = lazy(
  () => import('../features/supplier-orders/SupplierOrderDetailPage')
);
const SupplierOrderPrintPage = lazy(
  () => import('../features/reports/SupplierOrderPrintPage')
);
const MachinesPage = lazy(() => import('../features/machines/MachinesPage'));
const WorkCalendarPage = lazy(() => import('../features/calendar/WorkCalendarPage'));
const ScheduleBoardPage = lazy(() => import('../features/schedule/ScheduleBoardPage'));
const DashboardPage = lazy(() => import('../features/dashboard/DashboardPage'));
const ReportsHubPage = lazy(() => import('../features/reports/ReportsHubPage'));
const EfficiencyReportPage = lazy(() => import('../features/reports/EfficiencyReportPage'));
const InventoryReportPage = lazy(() => import('../features/reports/InventoryReportPage'));
const WorkerPerformanceReportPage = lazy(
  () => import('../features/reports/WorkerPerformanceReportPage')
);
const JobOrderPrintPage = lazy(() => import('../features/reports/JobOrderPrintPage'));
const AnalyticsLayout = lazy(() => import('../features/analytics/AnalyticsLayout'));
const AnalyticsOverviewPage = lazy(() => import('../features/analytics/AnalyticsOverviewPage'));
const AnalyticsProductionPage = lazy(
  () => import('../features/analytics/AnalyticsProductionPage')
);
const AnalyticsDelaysPage = lazy(() => import('../features/analytics/AnalyticsDelaysPage'));
const AnalyticsSalesPage = lazy(() => import('../features/analytics/AnalyticsSalesPage'));
const AnalyticsInventoryPage = lazy(() => import('../features/analytics/AnalyticsInventoryPage'));
const AnalyticsSuppliersPage = lazy(() => import('../features/analytics/AnalyticsSuppliersPage'));

function ReportsRedirect() {
  const rest = useParams()['*'];
  return <Navigate to={rest ? `/analytics/reports/${rest}` : '/analytics/reports'} replace />;
}

function WorkerSetupFromUser() {
  const { id } = useParams();
  return <Navigate to={id ? `/worker-setup?tab=roster&worker=${id}` : '/worker-setup'} replace />;
}

/** Workers already get the offline queue from their layout; an Admin recording work needs it here. */

function RouteSuspense({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<PageSkeleton variant="cards" />}>{children}</Suspense>;
}

export default function AppRoutes() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <IdleSessionWatcher />
        <NavMemoryProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/set-password" element={<SetPasswordPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            <Route path="/privacy" element={<PrivacyPolicyPage />} />

            <Route element={<ProtectedRoute />}>
              <Route
                element={
                  <ProtectedRoute roles={['ADMIN', 'OFFICE_STAFF', 'PRODUCTION_WORKER']} />
                }
              >
                <Route
                  path="/job-orders/:id/print"
                  element={
                    <RouteSuspense>
                      <JobOrderPrintPage />
                    </RouteSuspense>
                  }
                />
              </Route>
              <Route element={<ProtectedRoute roles={['ADMIN', 'OFFICE_STAFF']} />}>
                <Route
                  path="/supplier-orders/:id/print"
                  element={
                    <RouteSuspense>
                      <SupplierOrderPrintPage />
                    </RouteSuspense>
                  }
                />
              </Route>

              {/*
                Single RoleAwareLayout so office AppLayout (and worker shell) stay
                mounted across section switches — enables keep-alive + last-path nav.
              */}
              <Route
                element={
                  <ProtectedRoute roles={['ADMIN', 'OFFICE_STAFF', 'PRODUCTION_WORKER']} />
                }
              >
                <Route element={<RoleAwareLayout />}>
                  <Route
                    path="/schedule"
                    element={
                      <RouteSuspense>
                        <ScheduleBoardPage />
                      </RouteSuspense>
                    }
                  />
                  <Route
                    path="/account/security"
                    element={
                      <RouteSuspense>
                        <AccountSecurityPage />
                      </RouteSuspense>
                    }
                  />

                  <Route element={<ProtectedRoute roles={['ADMIN', 'OFFICE_STAFF']} />}>
                    <Route
                      path="/notifications"
                      element={
                        <RouteSuspense>
                          <NotificationsPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/dashboard"
                      element={
                        <RouteSuspense>
                          <DashboardPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/job-orders"
                      element={
                        <RouteSuspense>
                          <JobOrderListPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/job-orders/archived"
                      element={
                        <RouteSuspense>
                          <JobOrderListPage mode="archived" />
                        </RouteSuspense>
                      }
                    />
                    <Route element={<ProtectedRoute roles={['OFFICE_STAFF']} />}>
                      <Route
                        path="/job-orders/new"
                        element={
                          <RouteSuspense>
                            <JobOrderFormPage />
                          </RouteSuspense>
                        }
                      />
                    </Route>
                    <Route
                      path="/job-orders/:id/edit"
                      element={
                        <RouteSuspense>
                          <JobOrderFormPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/analytics"
                      element={
                        <RouteSuspense>
                          <AnalyticsLayout />
                        </RouteSuspense>
                      }
                    >
                      <Route index element={<AnalyticsOverviewPage />} />
                      <Route element={<ProtectedRoute roles={['ADMIN']} />}>
                        <Route path="production" element={<AnalyticsProductionPage />} />
                        <Route path="delays" element={<AnalyticsDelaysPage />} />
                      </Route>
                      <Route path="workers" element={<Navigate to="/analytics/production" replace />} />
                      <Route path="sales" element={<AnalyticsSalesPage />} />
                      <Route path="inventory" element={<AnalyticsInventoryPage />} />
                      <Route path="suppliers" element={<AnalyticsSuppliersPage />} />
                      <Route path="reports" element={<ReportsHubPage />} />
                      <Route path="reports/inventory" element={<InventoryReportPage />} />
                      <Route element={<ProtectedRoute roles={['ADMIN']} />}>
                        <Route path="reports/efficiency" element={<EfficiencyReportPage />} />
                        <Route
                          path="reports/worker-performance"
                          element={<WorkerPerformanceReportPage />}
                        />
                      </Route>
                      <Route path="efficiency" element={<Navigate to="/analytics/production" replace />} />
                      <Route path="capacity" element={<Navigate to="/analytics/production" replace />} />
                      <Route path="forecast" element={<Navigate to="/analytics/sales" replace />} />
                      <Route path="purchasing" element={<Navigate to="/analytics/suppliers" replace />} />
                    </Route>
                    <Route
                      path="/tools"
                      element={
                        <RouteSuspense>
                          <ToolsPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/clients"
                      element={
                        <RouteSuspense>
                          <ClientsPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/clients/:id"
                      element={
                        <RouteSuspense>
                          <ClientDetailPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/suppliers"
                      element={
                        <RouteSuspense>
                          <SuppliersAndOrdersPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/supplier-orders"
                      element={
                        <RouteSuspense>
                          <SuppliersAndOrdersPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/supplier-orders/:id"
                      element={
                        <RouteSuspense>
                          <SupplierOrderDetailPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/work-calendar"
                      element={
                        <RouteSuspense>
                          <WorkCalendarPage />
                        </RouteSuspense>
                      }
                    />
                    <Route path="/reports/*" element={<ReportsRedirect />} />
                  </Route>

                  <Route element={<ProtectedRoute roles={['ADMIN']} />}>
                    <Route
                      path="/machines"
                      element={
                        <RouteSuspense>
                          <MachinesPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/job-orders/:id/plan"
                      element={
                        <RouteSuspense>
                          <JobOrderPlanningPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/users"
                      element={
                        <RouteSuspense>
                          <UsersPage />
                        </RouteSuspense>
                      }
                    />
                    <Route path="/users/:id" element={<WorkerSetupFromUser />} />
                    <Route
                      path="/worker-setup"
                      element={
                        <RouteSuspense>
                          <WorkerSetupPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/attendance"
                      element={
                        <RouteSuspense>
                          <AttendancePage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/audit-log"
                      element={
                        <RouteSuspense>
                          <AuditLogPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/settings/shop"
                      element={
                        <RouteSuspense>
                          <ShopDetailsPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/settings/scoring-weights"
                      element={<Navigate to="/worker-setup" replace />}
                    />
                  </Route>

                  <Route
                    path="/job-orders/:id"
                    element={
                      <RouteSuspense>
                        <JobOrderDetailPage />
                      </RouteSuspense>
                    }
                  />

                  {/* Assigned work is for production workers only; the Admin does
                      shop-floor work, if any, on a separate worker account. */}
                  <Route element={<ProtectedRoute roles={['PRODUCTION_WORKER']} />}>
                    <Route
                      path="/my-assignments"
                      element={
                        <RouteSuspense>
                          <MyAssignmentsPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/my-assignments/:id"
                      element={
                        <RouteSuspense>
                          <AssignmentDetailPage />
                        </RouteSuspense>
                      }
                    />
                  </Route>

                  <Route element={<ProtectedRoute roles={['PRODUCTION_WORKER']} />}>
                    <Route
                      path="/home"
                      element={
                        <RouteSuspense>
                          <WorkerHomePage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/scan"
                      element={
                        <RouteSuspense>
                          <ScanToolPage />
                        </RouteSuspense>
                      }
                    />
                    <Route
                      path="/inventory"
                      element={
                        <RouteSuspense>
                          <InventoryPage />
                        </RouteSuspense>
                      }
                    />
                    <Route path="/my-tools" element={<Navigate to="/inventory" replace />} />
                    <Route
                      path="/account/profile"
                      element={
                        <RouteSuspense>
                          <WorkerProfilePage />
                        </RouteSuspense>
                      }
                    />
                  </Route>

                  <Route path="/" element={<Navigate to="/login" replace />} />
                </Route>
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/login" replace />} />
          </Routes>
        </NavMemoryProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
