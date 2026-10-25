import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiRequest } from "../services/api";
import { ErrorMessage } from "../components/Feedback";

function formatDate(value, zone) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: zone || undefined }).format(new Date(value));
}

export default function DashboardPage() {
  const [summary, setSummary] = useState(null);
  const [interviews, setInterviews] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    Promise.all([apiRequest("/recruitment/dashboard/summary"), apiRequest("/interviews?upcoming_only=true")])
      .then(([counts, upcoming]) => {
        setSummary(counts);
        setInterviews(upcoming.slice(0, 5));
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);
  return (
    <div>
      <div className="page-heading"><div><div className="eyebrow">MONDAY, YOUR HIRING DESK</div><h1>Good morning<span className="heading-period">.</span></h1><p className="muted">A clear view of what needs your attention.</p></div><Link className="button button-primary" to="/recruitment">View recruitment <span>→</span></Link></div>
      <ErrorMessage>{error}</ErrorMessage>
      <section className="metric-grid">
        {[["Applications", summary?.total_applications ?? "—", "Across your open roles", "♙", "blue"], ["Needs review", summary?.needs_review ?? "—", "Ready for your review", "⌕", "amber"], ["In interviews", summary?.in_interview ?? "—", "Moving through the process", "◷", "violet"], ["Open roles", summary?.open_jobs ?? "—", "Accepting applications", "↗", "green"]].map(([title, value, caption, icon, color]) => <article className="metric-card" key={title}><div className={`metric-icon ${color}`}>{icon}</div><span>{title}</span><strong>{loading ? <i className="skeleton" /> : value}</strong><small>{caption}</small></article>)}
      </section>
      <div className="dashboard-grid">
        <section className="panel"><div className="panel-heading"><div><div className="eyebrow">UP NEXT</div><h2>Upcoming interviews</h2></div><Link to="/interviews" className="text-link">View schedule →</Link></div>
          {loading ? <div className="loading-row"><span className="spinner" />Loading schedule…</div> : interviews.length ? <div className="interview-list">{interviews.map(item => <div className="interview-row" key={item.id}><div className="date-tile"><strong>{new Date(item.starts_at).toLocaleDateString(undefined, { day: "2-digit" })}</strong><small>{new Date(item.starts_at).toLocaleDateString(undefined, { month: "short" })}</small></div><div className="interview-person"><b>{item.candidate_name}</b><span>{item.job_title} · {item.interview_type}</span></div><div className="interview-date">{formatDate(item.starts_at, item.timezone)}<small>{item.timezone}</small></div></div>)}</div> : <div className="empty-inline"><span>◷</span><div><b>Your calendar has breathing room.</b><p>Scheduled interviews will show up here.</p></div></div>}
        </section>
        <section className="panel pipeline-panel"><div className="eyebrow">YOUR NEXT STEP</div><h2>Thoughtful hiring,<br />one step at a time.</h2><p>Review new applicants, keep the pipeline moving, and make every interview count.</p><div className="pipeline-step"><span>01</span><div><b>Review new applications</b><small>Check parsed details before confirming</small></div></div><div className="pipeline-step"><span>02</span><div><b>Plan the conversation</b><small>Schedule the right people, at the right time</small></div></div><Link to="/recruitment" className="text-link">Go to your pipeline →</Link></section>
      </div>
    </div>
  );
}

