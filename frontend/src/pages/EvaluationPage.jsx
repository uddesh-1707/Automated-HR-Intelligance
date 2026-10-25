import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiRequest } from "../services/api";
import { ErrorMessage } from "../components/Feedback";

const OUTCOMES = [
  ["passed", "Passed", "Candidate moves on to the next configured round."],
  ["failed", "Failed", "Candidate will not proceed further."],
  ["on_hold", "On hold", "Decision pending; you or HR can resolve it later."]
];

function when(interview) {
  const start = new Date(interview.starts_at);
  const format = options => new Intl.DateTimeFormat(undefined, { ...options, timeZone: interview.timezone }).format(start);
  return `${format({ dateStyle: "full" })} · ${format({ timeStyle: "short" })} (${interview.timezone}) · ${interview.duration_minutes} min`;
}

export default function EvaluationPage() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [outcome, setOutcome] = useState("");
  const [feedback, setFeedback] = useState("");
  const [rating, setRating] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  function load() {
    return apiRequest(`/evaluations/${encodeURIComponent(token)}`)
      .then(result => { setData(result); setError(""); })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }
  useEffect(() => { load(); }, [token]);

  async function submit(event) {
    event.preventDefault();
    if (!window.confirm(`Submit this evaluation as "${OUTCOMES.find(item => item[0] === outcome)?.[1]}"? ${outcome === "on_hold" ? "You can resolve it later from this link." : "A final outcome cannot be changed from this page."}`)) return;
    setBusy(true);
    setError("");
    try {
      setDone(await apiRequest(`/evaluations/${encodeURIComponent(token)}`, { method: "POST", body: { outcome, feedback, rating: rating ? Number(rating) : null } }));
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="public-page"><div className="loading-row"><span className="spinner" />Opening evaluation…</div></div>;
  if (!data) return <div className="public-page"><div className="public-brand"><span className="brand-mark">H</span> HRMS Automated Intelligence</div><main className="public-card"><ErrorMessage>{error || "This evaluation link is not available."}</ErrorMessage></main></div>;

  const { candidate, job, round, interview, evaluation } = data;
  return (
    <div className="public-page">
      <div className="public-brand"><span className="brand-mark">H</span> HRMS Automated Intelligence <small>INTERVIEW EVALUATION</small></div>
      <div className="public-layout evaluation-layout">
        <header className="public-job-header">
          <div className="eyebrow">{round.name}</div>
          <h1>{candidate.name}</h1>
          <div className="public-job-meta"><span>{job.title}</span><span>{when(interview)}</span>{interview.status === "cancelled" && <span>Cancelled</span>}</div>
          <p className="muted">Private link for {data.interviewer_email}. It covers this candidate and round only and expires {new Date(data.expires_at).toLocaleDateString()}.</p>
        </header>
        <section className="panel evaluation-card">
          <h2>Candidate</h2>
          <dl className="schedule-facts">
            <div><dt>Email</dt><dd>{candidate.email}</dd></div>
            <div><dt>Phone</dt><dd>{candidate.phone || "Not provided"}</dd></div>
            <div><dt>Experience</dt><dd>{candidate.experience_years == null ? "Not stated" : `${candidate.experience_years} years`}</dd></div>
            <div><dt>Education</dt><dd>{candidate.education || "Not parsed"}</dd></div>
          </dl>
          {candidate.skills?.length > 0 && <div className="schedule-tags">{candidate.skills.slice(0, 24).map(skill => <span className="pipeline-chip" key={skill}>{skill}</span>)}</div>}
          {data.screening_summary && <p className="muted evaluation-summary"><b>Resume screening note:</b> {data.screening_summary}</p>}
          {candidate.projects && <details><summary>Projects (from resume)</summary><pre className="evaluation-text">{candidate.projects}</pre></details>}
          {candidate.experience && <details><summary>Experience (from resume)</summary><pre className="evaluation-text">{candidate.experience}</pre></details>}
          <a className="button button-quiet" href={data.resume_url} target="_blank" rel="noreferrer">Open resume ↗</a>
          {interview.meeting_url && <a className="button button-quiet" href={interview.meeting_url} target="_blank" rel="noreferrer">Join meeting ↗</a>}
        </section>
        <section className="panel evaluation-card">
          <h2>Your evaluation</h2>
          <ErrorMessage>{error}</ErrorMessage>
          {done && <div className="alert alert-success" role="status"><span>✓</span>{done.message}</div>}
          {evaluation.submitted && <div className="alert alert-info" role="status">Recorded outcome: <b>{evaluation.outcome.replace("_", " ")}</b>{evaluation.feedback ? ` — ${evaluation.feedback}` : ""}</div>}
          {evaluation.can_submit ? <form className="stack-form" onSubmit={submit}>
            <fieldset className="outcome-options"><legend>Outcome</legend>{OUTCOMES.map(([value, label, hint]) => <label key={value} className={`outcome-option${outcome === value ? " selected" : ""}`}><input type="radio" name="outcome" required value={value} checked={outcome === value} onChange={() => setOutcome(value)} /><b>{label}</b><small>{hint}</small></label>)}</fieldset>
            <label>Feedback<textarea required minLength="10" maxLength="5000" rows="6" value={feedback} onChange={event => setFeedback(event.target.value)} placeholder="Strengths, concerns and evidence from the interview" /></label>
            <label>Overall rating (optional)<select value={rating} onChange={event => setRating(event.target.value)}><option value="">No rating</option>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value} / 5</option>)}</select></label>
            <button className="button button-primary" disabled={busy || !outcome}>{busy ? "Submitting…" : evaluation.resolves_on_hold ? "Resolve on-hold decision" : "Submit evaluation"}</button>
          </form> : !evaluation.submitted && <p className="muted">{interview.status === "cancelled" ? "This interview was cancelled." : "This evaluation can no longer be submitted from this link."}</p>}
        </section>
      </div>
    </div>
  );
}
