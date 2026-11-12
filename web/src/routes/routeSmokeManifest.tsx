import type { ComponentType } from 'react';
import type { UserRole } from '../types';
import ShopDetailsPage from '../features/settings/ShopDetailsPage';
import ScheduleBoardPage from '../features/schedule/ScheduleBoardPage';
import WorkCalendarPage from '../features/calendar/WorkCalendarPage';
import JobOrderListPage from '../features/job-orders/JobOrderListPage';
import JobOrderFormPage from '../features/job-orders/JobOrderFormPage';
import ToolsPage from '../features/tool-tracking/ToolsPage';
import DashboardPage from '../features/dashboard/DashboardPage';
import ClientsPage from '../features/clients/ClientsPage';
import UsersPage from '../features/users/UsersPage';
import MachinesPage from '../features/machines/MachinesPage';
import AttendancePage from '../features/attendance/AttendancePage';
import AuditLogPage from '../features/audit/AuditLogPage';
import NotificationsPage from '../features/notifications/NotificationsPage';
import WorkerSetupPage from '../features/workers/WorkerSetupPage';
import SuppliersAndOrdersPage from '../features/suppliers/SuppliersAndOrdersPage';
import AccountSecurityPage from '../features/auth/AccountSecurityPage';
import LoginPage from '../features/auth/LoginPage';
import WorkerHomePage from '../features/worker/WorkerHomePage';
import InventoryPage from '../features/worker/InventoryPage';
import MyAssignmentsPage from '../features/my-assignments/MyAssignmentsPage';
import ScanToolPage from '../features/tool-tracking/ScanToolPage';
import WorkerProfilePage from '../features/worker/WorkerProfilePage';

export type SmokeRole = Extract<UserRole, 'ADMIN' | 'OFFICE_STAFF' | 'PRODUCTION_WORKER'>;

export type RouteSmoke = {
  path: string;
  /** Roles allowed to open this page in production. */
  roles: SmokeRole[];
  Page: ComponentType;
};

/**
 * Every primary app page we smoke-render. A throw on mount (e.g. useBlocker
 * under BrowserRouter) fails the suite — this is what caught Shop details.
 */
export const ROUTE_SMOKES: RouteSmoke[] = [
  { path: '/login', roles: ['ADMIN', 'OFFICE_STAFF', 'PRODUCTION_WORKER'], Page: LoginPage },
  { path: '/account/security', roles: ['ADMIN', 'OFFICE_STAFF', 'PRODUCTION_WORKER'], Page: AccountSecurityPage },
  { path: '/schedule', roles: ['ADMIN', 'OFFICE_STAFF', 'PRODUCTION_WORKER'], Page: ScheduleBoardPage },
  { path: '/dashboard', roles: ['ADMIN', 'OFFICE_STAFF'], Page: DashboardPage },
  { path: '/notifications', roles: ['ADMIN', 'OFFICE_STAFF'], Page: NotificationsPage },
  { path: '/job-orders', roles: ['ADMIN', 'OFFICE_STAFF'], Page: JobOrderListPage },
  {
    path: '/job-orders/archived',
    roles: ['ADMIN', 'OFFICE_STAFF'],
    Page: () => <JobOrderListPage mode="archived" />,
  },
  { path: '/job-orders/new', roles: ['OFFICE_STAFF'], Page: JobOrderFormPage },
  { path: '/tools', roles: ['ADMIN', 'OFFICE_STAFF'], Page: ToolsPage },
  { path: '/clients', roles: ['ADMIN', 'OFFICE_STAFF'], Page: ClientsPage },
  { path: '/suppliers', roles: ['ADMIN', 'OFFICE_STAFF'], Page: SuppliersAndOrdersPage },
  { path: '/machines', roles: ['ADMIN'], Page: MachinesPage },
  { path: '/work-calendar', roles: ['ADMIN', 'OFFICE_STAFF'], Page: WorkCalendarPage },
  { path: '/users', roles: ['ADMIN'], Page: UsersPage },
  { path: '/worker-setup', roles: ['ADMIN'], Page: WorkerSetupPage },
  { path: '/attendance', roles: ['ADMIN'], Page: AttendancePage },
  { path: '/audit-log', roles: ['ADMIN'], Page: AuditLogPage },
  { path: '/settings/shop', roles: ['ADMIN'], Page: ShopDetailsPage },
  { path: '/home', roles: ['PRODUCTION_WORKER'], Page: WorkerHomePage },
  { path: '/inventory', roles: ['PRODUCTION_WORKER'], Page: InventoryPage },
  { path: '/my-assignments', roles: ['PRODUCTION_WORKER'], Page: MyAssignmentsPage },
  { path: '/scan', roles: ['PRODUCTION_WORKER'], Page: ScanToolPage },
  { path: '/account/profile', roles: ['PRODUCTION_WORKER'], Page: WorkerProfilePage },
];
