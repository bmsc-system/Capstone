import type { ReactNode } from 'react';
import './AuthShell.css';

const BUSINESS = 'Brothers Machine Shop and Services Corporation';
const TAGLINE = 'Every job order, every machine, on schedule.';

/** A quiet slice of the schedule board: machines down the side, jobs as bars. */
const BOARD: { machine: string; bars: { left: number; width: number; tone?: 'gold' | 'light' }[] }[] = [
  {
    machine: 'Lathe',
    bars: [
      { left: 1, width: 29, tone: 'light' },
      { left: 33, width: 22, tone: 'gold' },
      { left: 60, width: 30 },
    ],
  },
  {
    machine: 'Milling',
    bars: [
      { left: 6, width: 38 },
      { left: 50, width: 20, tone: 'light' },
    ],
  },
  {
    machine: 'Welding',
    bars: [
      { left: 0, width: 18, tone: 'gold' },
      { left: 22, width: 34, tone: 'light' },
      { left: 62, width: 16 },
    ],
  },
  {
    machine: 'Fitting',
    bars: [
      { left: 12, width: 26 },
      { left: 44, width: 40, tone: 'gold' },
    ],
  },
];

function Mark() {
  return (
    <div className="auth-mark">
      <img src="/logo.png" alt="Brothers Machine Shop logo" />
      <b>{BUSINESS}</b>
    </div>
  );
}

function Title() {
  return (
    <h1 className="auth-title">
      Production <em>Scheduling</em> System
    </h1>
  );
}

/**
 * Sign-in and account pages: the shop's brand panel on the left (on phones,
 * a short header) and the page's card on the right.
 */
export default function AuthShell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="auth">
      <aside className="auth__brand">
        <div />
        <div className="auth__hero">
          <Mark />
          <div className="auth__rule" />
          <Title />
          <p className="auth__tag">{TAGLINE}</p>
          <div className="auth-board" aria-hidden>
            <div className="auth-board__days">
              <span />
              {['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].map((d) => (
                <span key={d}>{d}</span>
              ))}
            </div>
            {BOARD.map((row) => (
              <div className="auth-board__row" key={row.machine}>
                <span>{row.machine}</span>
                <div className="auth-board__track">
                  {row.bars.map((bar, i) => (
                    <i
                      key={i}
                      className={bar.tone ? `is-${bar.tone}` : undefined}
                      style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                    />
                  ))}
                </div>
              </div>
            ))}
            <div className="auth-board__now" />
          </div>
        </div>
        <div className="auth__address">J.P. Laurel National Highway, San Pioquinto, Malvar, Batangas</div>
      </aside>

      <main className="auth__side">
        <header className="auth__mhead">
          <Mark />
          <Title />
          <p className="auth__tag">{TAGLINE}</p>
        </header>
        <div className={`login-card${wide ? ' login-card--wide' : ''}`}>{children}</div>
      </main>
    </div>
  );
}

/** The card's bottom row: copyright on the left, a link on the right. */
export function AuthFooter({ children }: { children?: ReactNode }) {
  return (
    <div className="login-card__legal">
      <span>&copy; {new Date().getFullYear()} Brothers Machine Shop</span>
      {children ?? <a href="/privacy">Privacy</a>}
    </div>
  );
}
