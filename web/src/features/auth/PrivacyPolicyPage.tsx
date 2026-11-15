import { Link } from 'react-router-dom';
import AuthShell, { AuthFooter } from './AuthShell';
import './LoginPage.css';

/** The shop's own email, the same one printed on its documents. */
const CONTACT_EMAIL = 'brothersmachining@yahoo.com';

export default function PrivacyPolicyPage() {
  return (
    <AuthShell wide>
      <div className="login-card__body privacy-policy">
        <div className="login-card__eyebrow">Privacy</div>
        <h2 className="login-card__title">Privacy Policy</h2>

        <h2>What we collect</h2>
        <ul>
          <li>User account details of shop staff (name, email, mobile number, and role).</li>
          <li>Client names, email addresses, and mobile numbers.</li>
          <li>Job order and production records.</li>
        </ul>

        <h2>Why we collect it</h2>
        <p>
          We use this information to schedule work, monitor production, and send clients
          job-status updates by email when they have agreed to receive them.
        </p>

        <h2>How we store and share it</h2>
        <p>
          Data is stored in a secured cloud database. We do not sell it or share it with anyone,
          except our email delivery service (Resend), and only so it can deliver notifications.
        </p>

        <h2>Your choices</h2>
        <p>
          Clients can ask to stop receiving updates, or to have their contact details removed, by
          contacting the shop.
        </p>

        <h2>Data Privacy Act</h2>
        <p>
          We handle personal information in compliance with the Data Privacy Act of 2012
          (Republic Act No. 10173).
        </p>

        <h2>Contact</h2>
        <p>
          For privacy questions or requests, email{' '}
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
        </p>

        <AuthFooter>
          <Link className="login-card__back" to="/login">
            Back to sign in
          </Link>
        </AuthFooter>
      </div>
    </AuthShell>
  );
}
