import { STAY_OPEN } from '../components/ui/stayOpen';
import { ConfigProvider, Popover } from 'antd';
import {
  CalendarOutlined,
  HomeOutlined,
  IdcardOutlined,
  LeftOutlined,
  SafetyCertificateOutlined,
  LogoutOutlined,
  ProfileOutlined,
  QrcodeOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { createContext, useContext, useState, type ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { confirmLogout } from '../utils/confirmLogout';
import { workerTheme } from '../theme/antTheme';
import { colors as tokenColors, tones } from '../theme/tokens';
import PinOfferModal from '../features/auth/PinOfferModal';
import KeepAliveOutlet from '../navigation/KeepAliveOutlet';
import RouteErrorBoundary from '../components/RouteErrorBoundary';
import { useNavMemory } from '../navigation/navMemory';
import NeedsConnection from '../offline/NeedsConnection';
import OfflineStatusBar from '../offline/OfflineStatusBar';
import { useOffline } from '../offline/OfflineProvider';
import { offlineCache } from '../offline/offlineCache';
import { PersonAvatar } from '../components/PersonAvatar';
import { MyOperationsProvider } from '../features/my-assignments/useMyOperations';
import WorkerBell from '../features/worker/WorkerBell';
import './topBar.css';
import '../features/worker/worker.css';
import './appShell.css';

export interface WorkerPalette {
  bg: string;
  card: string;
  cardBorder: string;
  navBg: string;
  headerBg: string;
  headerText: string;
  accent: string;
  accentSoft: string;
  green: string;
  greenSoft: string;
  amber: string;
  red: string;
  text: string;
  textSecondary: string;
  chipBg: string;
  shadow: string;
  inputBg: string;
}

const lightPalette: WorkerPalette = {
  bg: tokenColors.page,
  card: tokenColors.surface,
  cardBorder: tokenColors.border,
  navBg: tokenColors.surface,
  headerBg: tokenColors.navy,
  headerText: tokenColors.textOnColor,
  accent: tokenColors.primary,
  accentSoft: '#3b82f6',
  green: tones.green.fg,
  greenSoft: tones.green.bg,
  amber: tones.amber.fg,
  red: tones.red.fg,
  text: tokenColors.text,
  textSecondary: tokenColors.textMuted,
  chipBg: tokenColors.surfaceSunken,
  shadow: '0 1px 2px rgba(15,23,42,0.04)',
  inputBg: tokenColors.surface,
};

interface WorkerThemeValue {
  colors: WorkerPalette;
  logout: () => void;
}

const WorkerThemeContext = createContext<WorkerThemeValue>({
  colors: lightPalette,
  logout: () => {},
});

export function useWorkerTheme() {
  return useContext(WorkerThemeContext);
}

export function WorkerPageHeader({
  title,
  subtitle,
  onBack,
  right,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  onBack?: () => void;
  right?: ReactNode;
}) {
  const { logout } = useWorkerTheme();
  const { user, isWorker } = useAuth();
  const navigate = useNavigate();
  const { clearSectionCache } = useNavMemory();
  const [accountOpen, setAccountOpen] = useState(false);

  const go = (path: string) => {
    setAccountOpen(false);
    clearSectionCache('/account');
    navigate(path);
  };

  const accountPanel = user ? (
    <div className="acct-panel">
      <div className="acct-panel__who">
        <PersonAvatar userId={user.id} fullName={user.fullName} photoVersion={user.photoVersion} size={40} />
        <div style={{ minWidth: 0 }}>
          <div className="acct-panel__name">{user.fullName}</div>
          <div className="acct-panel__role">{isWorker ? 'Production Worker' : 'Administrator'}</div>
        </div>
      </div>
      {isWorker && (
        <button type="button" className="acct-sheet-btn" onClick={() => go('/account/profile')}>
          <IdcardOutlined />
          My profile
        </button>
      )}
      <button type="button" className="acct-sheet-btn" onClick={() => go('/account/security')}>
        <SafetyCertificateOutlined />
        Account security
      </button>
      <button
        type="button"
        className="acct-sheet-btn acct-sheet-btn--danger"
        onClick={() => {
          setAccountOpen(false);
          logout();
        }}
      >
        <LogoutOutlined />
        Log out
      </button>
    </div>
  ) : null;

  return (
    <header className={`w-top${onBack ? ' w-top--with-back' : ''}`}>
      <div className="w-top__row">
        {onBack && (
          <button type="button" className="w-iconbtn" onClick={onBack} aria-label="Back">
            <LeftOutlined style={{ fontSize: 18 }} />
          </button>
        )}
        <div className="w-top__titles">
          <div className="w-top__title">{title}</div>
          {subtitle && <div className="w-top__sub">{subtitle}</div>}
        </div>
        {right}
        <OfflineStatusBar />
        <WorkerBell />
        {user && (
          <Popover
            trigger="click"
            placement="bottomRight"
            open={accountOpen}
            onOpenChange={setAccountOpen}
            arrow={false}
            content={accountPanel}
            styles={{ container: { padding: 12, borderRadius: 12 } }}
          >
            <button type="button" className="w-iconbtn" aria-label="Account menu">
              <span className="w-avatar">
                <PersonAvatar
                  userId={user.id}
                  fullName={user.fullName}
                  photoVersion={user.photoVersion}
                  size={30}
                />
              </span>
            </button>
          </Popover>
        )}
      </div>
    </header>
  );
}

/** Pages that work offline: the dashboard and the jobs (with their job pages). */
const OFFLINE_PATHS = ['/home', '/my-assignments'];

const TABS = [
  { key: '/home', label: 'Dashboard', icon: <HomeOutlined /> },
  { key: '/my-assignments', label: 'Jobs', icon: <ProfileOutlined /> },
  { key: '/scan', label: 'Scan', icon: <QrcodeOutlined />, center: true },
  { key: '/schedule', label: 'Schedule', icon: <CalendarOutlined /> },
  { key: '/inventory', label: 'Inventory', icon: <ToolOutlined /> },
];

function isTabActive(key: string, pathname: string) {
  if (key === '/my-assignments') {
    return pathname.startsWith('/my-assignments') || pathname.startsWith('/job-orders/');
  }
  return pathname === key || pathname.startsWith(`${key}/`);
}

export default function WorkerLayout() {
  const { user, logout: authLogout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { resolveSectionNav } = useNavMemory();
  const { online, pending } = useOffline();
  const colors = lightPalette;
  const isScan = location.pathname.startsWith('/scan');
  const needsConnection = !online && !OFFLINE_PATHS.some((p) => location.pathname.startsWith(p));

  const logout = () => {
    const waiting = pending.length;
    confirmLogout(
      () => {
        if (user) offlineCache.clear(user.id);
        authLogout();
        try {
          sessionStorage.removeItem('bmsc.navMemory.v1');
        } catch {
          /* ignore */
        }
        navigate('/login');
      },
      waiting
        ? `${waiting === 1 ? '1 action is' : `${waiting} actions are`} still waiting to send. ` +
            'They stay on this phone and are sent after you sign in again.'
        : 'You will need to sign in again to see your jobs.'
    );
  };

  const goSection = (sectionKey: string) => {
    const { to } = resolveSectionNav(sectionKey);
    navigate(to);
  };

  return (
    <WorkerThemeContext.Provider value={{ colors, logout }}>
      <ConfigProvider theme={workerTheme} modal={STAY_OPEN}>
        <MyOperationsProvider>
          <div
            className="worker-shell"
            style={{
              height: '100%',
              background: isScan ? '#000' : colors.bg,
              color: colors.text,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <main className={`w-main${isScan && !needsConnection ? ' w-main--scan' : ''}`}>
              {needsConnection && (
                <>
                  <WorkerPageHeader title="Offline" />
                  <NeedsConnection />
                </>
              )}
              <div style={needsConnection ? { display: 'none' } : undefined}>
                <RouteErrorBoundary resetKey={location.pathname}>
                  <KeepAliveOutlet />
                </RouteErrorBoundary>
              </div>
            </main>
            <PinOfferModal />

            <nav className="w-nav" aria-label="Main">
              {TABS.map((tab) => {
                const active = isTabActive(tab.key, location.pathname);
                if (tab.center) {
                  return (
                    <button
                      key={tab.key}
                      type="button"
                      className={`w-nav__scan${active ? ' is-active' : ''}`}
                      onClick={() => goSection(tab.key)}
                      aria-label={tab.label}
                      aria-current={active ? 'page' : undefined}
                    >
                      <span className="w-nav__fab">{tab.icon}</span>
                      <span>{tab.label}</span>
                    </button>
                  );
                }
                return (
                  <button
                    key={tab.key}
                    type="button"
                    className={`w-nav__item${active ? ' is-active' : ''}`}
                    onClick={() => goSection(tab.key)}
                    aria-current={active ? 'page' : undefined}
                  >
                    {tab.icon}
                    <span>{tab.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>
        </MyOperationsProvider>
      </ConfigProvider>
    </WorkerThemeContext.Provider>
  );
}
