import { STAY_OPEN } from '../components/ui/stayOpen';
import { ConfigProvider, Grid, Layout, Menu, Button, Drawer, Popover, Tooltip } from 'antd';
import type { MenuProps } from 'antd';
import {
  LogoutOutlined,
  FileTextOutlined,
  TeamOutlined,
  ToolOutlined,
  SettingOutlined,
  BarChartOutlined,
  CalendarOutlined,
  ContactsOutlined,
  BuildOutlined,
  MenuOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  FieldTimeOutlined,
  ShopOutlined,
  ClockCircleOutlined,
  SafetyCertificateOutlined,
  DownOutlined,
  DashboardOutlined,
  BankOutlined,
  LeftOutlined,
  CloseOutlined,
  AuditOutlined,
} from '@ant-design/icons';
import { useEffect, useState, type ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { confirmLogout } from '../utils/confirmLogout';
import KeepAliveOutlet from '../navigation/KeepAliveOutlet';
import { getNavSection, useNavMemory } from '../navigation/navMemory';
import RouteErrorBoundary from '../components/RouteErrorBoundary';
import NotificationBell from '../components/NotificationBell';
import { PersonAvatar } from '../components/PersonAvatar';
import { Breadcrumbs, type Crumb } from '../components/ui/PageHeader';
import { layout } from '../theme/tokens';
import { officeCompactTheme } from '../theme/antTheme';
import { MOBILE_SHELL_QUERY, RAIL_QUERY, useMediaQuery } from '../hooks/useViewport';
import { formatShop, shopNow } from '../utils/shopTime';
import { PageHeaderActionsProvider, usePageHeaderActionsSlot } from './pageHeaderActions';
import './topBar.css';
import './appShell.css';

const { Header, Sider, Content } = Layout;

type NavItem = { key: string; icon: ReactNode; label: string; adminOnly?: boolean };
type NavGroup = { label: string; items: NavItem[] };

/** Same groups and names for Admin and Office Staff; each sees only what it can open. */
const NAV: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      { key: '/dashboard', icon: <DashboardOutlined />, label: 'Dashboard' },
      { key: '/schedule', icon: <CalendarOutlined />, label: 'Schedule' },
    ],
  },
  {
    label: 'Production',
    items: [
      { key: '/job-orders', icon: <FileTextOutlined />, label: 'Job Orders' },
      { key: '/machines', icon: <BuildOutlined />, label: 'Machines', adminOnly: true },
      { key: '/work-calendar', icon: <FieldTimeOutlined />, label: 'Work calendar' },
    ],
  },
  {
    label: 'Purchasing & stock',
    items: [
      { key: '/suppliers', icon: <ShopOutlined />, label: 'Suppliers and Orders' },
      { key: '/tools', icon: <ToolOutlined />, label: 'Inventory' },
    ],
  },
  {
    label: 'Clients',
    items: [{ key: '/clients', icon: <ContactsOutlined />, label: 'Clients' }],
  },
  {
    label: 'People',
    items: [
      { key: '/users', icon: <TeamOutlined />, label: 'Users & Roles', adminOnly: true },
      { key: '/worker-setup', icon: <SettingOutlined />, label: 'Worker setup', adminOnly: true },
      { key: '/attendance', icon: <ClockCircleOutlined />, label: 'Attendance', adminOnly: true },
      { key: '/audit-log', icon: <AuditOutlined />, label: 'Audit log', adminOnly: true },
      { key: '/settings/shop', icon: <BankOutlined />, label: 'Shop details', adminOnly: true },
    ],
  },
  {
    label: 'Insights',
    items: [{ key: '/analytics', icon: <BarChartOutlined />, label: 'Analytics' }],
  },
];

const SECTION_META: Record<string, { title: string; subtitle?: string }> = {
  '/schedule': { title: 'Schedule' },
  '/job-orders': { title: 'Job Orders' },
  '/machines': { title: 'Machines' },
  '/work-calendar': { title: 'Work calendar' },
  '/suppliers': { title: 'Suppliers and Orders' },
  '/tools': { title: 'Inventory' },
  '/clients': { title: 'Clients' },
  '/users': { title: 'Users & Roles' },
  '/worker-setup': { title: 'Worker setup' },
  '/attendance': { title: 'Attendance' },
  '/analytics': { title: 'Analytics' },
  '/audit-log': { title: 'Audit log' },
  '/settings/shop': { title: 'Shop details' },
  '/account': { title: 'Account security' },
};

const REPORT_TITLES: Record<string, string> = {
  efficiency: 'Efficiency report',
  inventory: 'Inventory Status',
  'worker-performance': 'Worker Performance',
};

type Meta = { title: string; subtitle?: string; crumbs?: Crumb[] };

function routeMeta(pathname: string, section: string | null): Meta {
  const jobs: Crumb = { label: 'Job Orders', to: '/job-orders' };
  if (pathname === '/job-orders/archived') {
    return {
      title: 'Archived job orders',
      crumbs: [jobs, { label: 'Archived' }],
    };
  }
  if (pathname === '/job-orders/new') return { title: 'New job order', crumbs: [jobs, { label: 'New' }] };
  if (/^\/job-orders\/[^/]+\/edit$/.test(pathname)) return { title: 'Edit job order', crumbs: [jobs, { label: 'Edit' }] };
  if (/^\/job-orders\/[^/]+\/plan$/.test(pathname)) {
    return { title: 'Plan job order', crumbs: [{ label: 'Job Orders', to: '/job-orders?tab=drafts' }, { label: 'Plan' }] };
  }
  if (/^\/job-orders\/[^/]+$/.test(pathname)) {
    return { title: 'Job order', crumbs: [jobs, { label: 'Details' }] };
  }
  if (/^\/clients\/[^/]+$/.test(pathname)) {
    return { title: 'Client', crumbs: [{ label: 'Clients', to: '/clients' }, { label: 'Details' }] };
  }
  if (/^\/supplier-orders\/[^/]+$/.test(pathname)) {
    return {
      title: 'Supplier order',
      crumbs: [{ label: 'Suppliers and Orders', to: '/supplier-orders' }, { label: 'Supplier order' }],
    };
  }
  const report = pathname.match(/^\/analytics\/reports\/([^/]+)/);
  if (report) {
    return {
      title: REPORT_TITLES[report[1]] || 'Report',
      crumbs: [
        { label: 'Analytics', to: '/analytics' },
        { label: 'Reports', to: '/analytics/reports' },
        { label: REPORT_TITLES[report[1]] || 'Report' },
      ],
    };
  }
  return SECTION_META[section || ''] || { title: '' };
}

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <div className={`app-brand${collapsed ? ' is-collapsed' : ''}`}>
      <img src="/logo.png" alt="Brothers Machine Shop and Services Corporation logo" className="app-brand__logo" />
      {!collapsed && (
        <div className="app-brand__copy">
          <div className="app-brand__name">Brothers Machine Shop and Services Corporation</div>
          <div className="app-brand__tag">Production Scheduling</div>
        </div>
      )}
    </div>
  );
}

function greeting() {
  const h = shopNow().hour();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

function firstName(nickname?: string | null, fullName?: string | null) {
  return nickname?.trim() || fullName?.trim().split(/\s+/)[0] || 'there';
}

function roleLabel(role?: string) {
  if (role === 'ADMIN') return 'Administrator';
  if (role === 'OFFICE_STAFF') return 'Office Staff';
  return role || '';
}

function AppLayoutShell() {
  // Tablet landscape and small laptops start with the icon rail so pages get the
  // width; the menu button still expands it. Crossing the breakpoint resets it.
  const railByDefault = useMediaQuery(RAIL_QUERY);
  const [collapsed, setCollapsed] = useState(railByDefault);
  useEffect(() => setCollapsed(railByDefault), [railByDefault]);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const screens = Grid.useBreakpoint();
  const isPhone = !screens.md;
  const mobileShell = useMediaQuery(MOBILE_SHELL_QUERY);
  // One breakpoint for both: below 1024px the navy top bar and the menu drawer,
  // from 1024px the sidebar. (992–1023px used to show both at once.)
  const isCompact = mobileShell;
  const { user, logout, isAdmin, isOfficeStaff } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { resolveSectionNav, clearSectionCache } = useNavMemory();
  const showBell = isAdmin || isOfficeStaff;
  const pageHeaderActions = usePageHeaderActionsSlot();

  const goSection = (sectionKey: string) => {
    const { to } = resolveSectionNav(sectionKey);
    navigate(to);
    setMobileOpen(false);
  };

  const groups = (isAdmin || isOfficeStaff ? NAV : [])
    .map((g) => {
      const items = g.items.filter((it) => isAdmin || !it.adminOnly);
      return { ...g, items };
    })
    .filter((g) => g.items.length > 0);

  const section = getNavSection(location.pathname);
  const allKeys = groups.flatMap((g) => g.items.map((it) => it.key));
  const selectedKey = allKeys.find((k) => k === section) || '';
  const baseMeta =
    section === '/dashboard'
      ? { title: `${greeting()}, ${firstName(user?.nickname, user?.fullName)}`, subtitle: formatShop(shopNow(), 'dddd, MMMM D, YYYY') }
      : routeMeta(location.pathname, section);
  const workerDetail = section === '/worker-setup' && new URLSearchParams(location.search).has('worker');
  const meta =
    mobileShell && workerDetail
      ? { title: 'Worker', crumbs: [{ label: 'Worker setup', to: '/worker-setup' }, { label: 'Details' }] }
      : baseMeta;
  const iconOnly = collapsed && !isCompact;

  const menuItems: MenuProps['items'] = groups.map((g) => ({
    type: 'group' as const,
    key: `group:${g.label}`,
    label: iconOnly ? <span className="app-nav__rule" aria-hidden /> : g.label,
    children: g.items.map((it) => ({
      key: it.key,
      icon: it.icon,
      label: it.label,
      title: it.label,
    })),
  }));

  const handleLogout = () => {
    confirmLogout(() => {
      logout();
      try {
        sessionStorage.removeItem('bmsc.navMemory.v1');
      } catch {
        /* ignore */
      }
      setMobileOpen(false);
      navigate('/login');
    });
  };

  const menu = (
    <Menu
      theme="dark"
      mode="inline"
      inlineCollapsed={iconOnly}
      selectedKeys={[selectedKey]}
      items={menuItems}
      className="app-nav"
      onClick={({ key }) => goSection(String(key))}
    />
  );

  const logoutButton = (compactBtn: boolean) => (
    <div className={`app-sider__foot${compactBtn ? ' is-collapsed' : ''}`}>
      <Tooltip title={compactBtn ? 'Log out' : undefined} placement="right">
        <Button type="text" icon={<LogoutOutlined />} onClick={handleLogout} block aria-label="Log out" className="app-sider__logout">
          {compactBtn ? null : 'Log out'}
        </Button>
      </Tooltip>
    </div>
  );

  const accountPanel = (
    <div className="acct-panel">
      <div className="acct-panel__who">
        <PersonAvatar userId={user?.id} fullName={user?.fullName} photoVersion={user?.photoVersion} size={40} />
        <div style={{ minWidth: 0 }}>
          <div className="acct-panel__name">{user?.fullName}</div>
          <div className="acct-panel__role">{roleLabel(user?.role)}</div>
        </div>
      </div>
      <button
        type="button"
        className="acct-sheet-btn"
        onClick={() => {
          setAccountOpen(false);
          clearSectionCache('/account');
          navigate('/account/security');
        }}
      >
        <SafetyCertificateOutlined />
        Account security
      </button>
      <button
        type="button"
        className="acct-sheet-btn acct-sheet-btn--danger"
        onClick={() => {
          setAccountOpen(false);
          handleLogout();
        }}
      >
        <LogoutOutlined />
        Log out
      </button>
    </div>
  );

  const accountChip = user?.fullName ? (
    <Popover
      trigger="click"
      placement="bottomRight"
      open={accountOpen}
      onOpenChange={setAccountOpen}
      content={accountPanel}
      styles={{ container: { padding: 12, borderRadius: 12 } }}
    >
      <button type="button" className={`app-topbar__who${accountOpen ? ' is-open' : ''}`} aria-label="Account">
        <PersonAvatar userId={user.id} fullName={user.fullName} photoVersion={user.photoVersion} size={32} />
        {!isPhone && (
          <span className="app-topbar__who-copy">
            <span className="app-topbar__who-name">{user.fullName}</span>
            <span className="app-topbar__who-role">{roleLabel(user.role)}</span>
          </span>
        )}
        {!isPhone && <DownOutlined className="app-topbar__who-caret" aria-hidden />}
      </button>
    </Popover>
  ) : null;

  const accountButton = user?.fullName ? (
    <Popover
      trigger="click"
      placement="bottomRight"
      open={accountOpen}
      onOpenChange={setAccountOpen}
      arrow={false}
      content={accountPanel}
      styles={{ container: { padding: 12, borderRadius: 12 } }}
    >
      <button type="button" className="w-iconbtn" aria-label="Account">
        <span className="w-avatar">
          <PersonAvatar userId={user.id} fullName={user.fullName} photoVersion={user.photoVersion} size={30} />
        </span>
      </button>
    </Popover>
  ) : null;

  const backTo = [...(meta.crumbs || [])].reverse().find((c) => c.to);

  const phoneTabs = [
    { key: '/dashboard', label: 'Dashboard', icon: <DashboardOutlined />, match: '/dashboard' },
    { key: '/job-orders', label: 'Jobs', icon: <FileTextOutlined />, match: '/job-orders' },
    { key: '/schedule', label: 'Schedule', icon: <CalendarOutlined />, match: '/schedule' },
    { key: '/tools', label: 'Inventory', icon: <ToolOutlined />, match: '/tools' },
  ].filter((t) => allKeys.includes(t.key));

  return (
    <ConfigProvider theme={mobileShell ? officeCompactTheme : undefined} modal={STAY_OPEN}>
    <Layout
      className={`app-shell${isPhone ? ' app-shell--phone' : ''}${isCompact ? ' app-shell--compact' : ''}${mobileShell ? ' app-shell--m' : ''}`}
      style={{ height: '100%', overflow: 'hidden' }}
    >
      {!isCompact && (
        <Sider
          trigger={null}
          collapsible
          collapsed={collapsed}
          collapsedWidth={layout.sidebarCollapsedWidth}
          width={layout.sidebarWidth}
          className="app-sider"
        >
          <Brand collapsed={collapsed} />
          <div className="app-sider__nav">{menu}</div>
          {logoutButton(collapsed)}
        </Sider>
      )}

      <Drawer
        placement="left"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        size={layout.sidebarWidth + 48}
        closable={false}
        className="app-nav-drawer"
        styles={{ section: { padding: 0, borderRadius: 0 }, header: { display: 'none' }, body: { padding: 0 } }}
      >
        <div className="app-nav-drawer__top">
          <Brand collapsed={false} />
          <button type="button" className="app-nav-drawer__close" onClick={() => setMobileOpen(false)} aria-label="Close menu">
            <CloseOutlined />
          </button>
        </div>
        <div className="app-sider__nav">{menu}</div>
        {logoutButton(false)}
      </Drawer>

      <Layout className="app-main">
        {mobileShell ? (
          <header className={`w-top app-mtop${backTo ? ' w-top--with-back' : ''}`}>
            <div className="w-top__row">
              {backTo ? (
                <button type="button" className="w-iconbtn" onClick={() => navigate(backTo.to!)} aria-label={`Back to ${backTo.label}`}>
                  <LeftOutlined style={{ fontSize: 18 }} />
                </button>
              ) : (
                <button
                  type="button"
                  className="w-iconbtn"
                  onClick={() => {
                    if (isCompact) setMobileOpen(true);
                    else setCollapsed(!collapsed);
                  }}
                  aria-label={isCompact ? 'Open menu' : collapsed ? 'Expand menu' : 'Collapse menu'}
                >
                  <MenuOutlined />
                </button>
              )}
              <div className="w-top__titles">
                <h1 className="w-top__title">{meta.title || 'BMSC'}</h1>
                {section === '/dashboard' && meta.subtitle ? <div className="w-top__sub">{meta.subtitle}</div> : null}
              </div>
              {pageHeaderActions ? <div className="app-mtop__actions">{pageHeaderActions}</div> : null}
              {showBell && <NotificationBell className="w-iconbtn" />}
              {accountButton}
            </div>
          </header>
        ) : (
        <Header className="app-topbar">
          <div className="app-topbar__left">
            {!isPhone && (
              <Tooltip title={isCompact ? 'Open menu' : collapsed ? 'Expand menu' : 'Collapse menu'}>
                <Button
                  type="text"
                  className="app-topbar__menu-btn"
                  icon={isCompact ? <MenuOutlined /> : collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  onClick={() => {
                    if (isCompact) setMobileOpen(true);
                    else setCollapsed(!collapsed);
                  }}
                  aria-label={isCompact ? 'Open menu' : collapsed ? 'Expand menu' : 'Collapse menu'}
                />
              </Tooltip>
            )}
            <div className="app-topbar__heading">
              {meta.crumbs?.length ? <Breadcrumbs items={meta.crumbs} /> : null}
              <div className="app-topbar__title-row">
                <h1 className="app-topbar__title">{meta.title || 'BMSC'}</h1>
                {meta.subtitle && !meta.crumbs?.length && !pageHeaderActions ? (
                  <span className="app-topbar__subtitle">{meta.subtitle}</span>
                ) : null}
              </div>
            </div>
          </div>
          <div className="app-topbar__right">
            {pageHeaderActions ? (
              <div className="app-topbar__page-actions">{pageHeaderActions}</div>
            ) : null}
            {showBell && <NotificationBell className="app-topbar__icon-btn app-topbar__bell" />}
            {accountChip}
          </div>
        </Header>
        )}
        <div className="app-shell__scroll">
          <Content className="app-shell__content">
            <RouteErrorBoundary resetKey={location.pathname}>
              <KeepAliveOutlet />
            </RouteErrorBoundary>
          </Content>
        </div>

        {isPhone && (
          <nav className="app-phone-nav" aria-label="Main">
            {phoneTabs.map((tab) => {
              const active = location.pathname.startsWith(tab.match);
              return (
                <button
                  key={tab.key}
                  type="button"
                  className={`app-phone-nav__item${active ? ' is-active' : ''}`}
                  onClick={() => goSection(tab.key)}
                >
                  <span className="app-phone-nav__icon">{tab.icon}</span>
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>
        )}
      </Layout>
    </Layout>
    </ConfigProvider>
  );
}

export default function AppLayout() {
  return (
    <PageHeaderActionsProvider>
      <AppLayoutShell />
    </PageHeaderActionsProvider>
  );
}
