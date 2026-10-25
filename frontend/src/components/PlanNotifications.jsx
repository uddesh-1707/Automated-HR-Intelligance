/** Honest delivery summary for a confirmed plan's shortlisting / interviewer emails, with a safe retry. */
export default function PlanNotifications({ notifications, busy, onResend }) {
  if (!notifications) return null;
  const { sent = 0, partial = 0, not_configured: notConfigured = 0, failed = 0 } = notifications;
  const problems = partial + notConfigured + failed;
  if (!sent && !problems) return null;
  return (
    <div className={`plan-notifications ${problems ? "plan-notifications-warn" : "plan-notifications-ok"}`} role="status">
      <div>
        <b>{problems ? "Some emails were not delivered" : "Emails delivered"}</b>
        <p>
          {sent > 0 && `${sent} candidate${sent === 1 ? "" : "s"} and their interviewers were emailed. `}
          {notConfigured > 0 && `Email is not configured, so ${notConfigured} notification${notConfigured === 1 ? "" : "s"} could not be sent (set SMTP_HOST and SMTP_FROM_EMAIL). `}
          {(partial > 0 || failed > 0) && `${partial + failed} had delivery problems. `}
          {problems > 0 && "The interviews themselves are booked. Retrying never sends a message twice."}
        </p>
      </div>
      {problems > 0 && onResend && <button type="button" className="button button-quiet button-small" disabled={busy} onClick={onResend}>{busy ? "Retrying…" : "Retry sending emails"}</button>}
    </div>
  );
}
