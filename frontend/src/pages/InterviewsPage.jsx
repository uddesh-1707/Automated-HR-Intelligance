import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import InterviewForm from "../components/InterviewForm";
import InterviewRoundEditor from "../components/InterviewRoundEditor";
import InterviewSchedulingAssistant from "../components/InterviewSchedulingAssistant";
import { EmptyState, ErrorMessage } from "../components/Feedback";
import { apiRequest } from "../services/api";
import { useToast } from "../components/Toast";
import { emitDataChanged, useDataRefresh } from "../services/events";

function formatWhen(item) {
  const start = new Date(item.starts_at);
  const end = new Date(start.getTime() + (item.duration_minutes || 60) * 60000);
  const zone = item.timezone;
  const date = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: zone }).format(start);
  const time = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", timeZone: zone });
  return { date, time: `${time.format(start)} – ${time.format(end)}` };
}

export default function InterviewsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [interviews, setInterviews] = useState([]);
  const [applications, setApplications] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [prefillId, setPrefillId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [outcomeDrafts, setOutcomeDrafts] = useState({});
  const toast = useToast();
  const load = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    Promise.all([apiRequest("/interviews"), apiRequest("/recruitment/applications"), apiRequest("/recruitment/jobs")])
      .then(([scheduled, applied, jobList]) => {
        setInterviews(scheduled);
        setApplications(applied);
        setJobs(jobList);
      })
      .catch(err => { if (!silent) setError(err.message); })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);
  // Your Schedule always shows persisted data: reload after any booking, on focus, and every minute.
  useDataRefresh(() => load(true), { interval: 60000 });
  useEffect(() => {
    const applicationId = searchParams.get("application");
    if (!applicationId || !applications.length) return;
    if (!applications.some(item => item.id === applicationId)) {
      setError("That application could not be found.");
      return;
    }
    setPrefillId(applicationId);
    setSelected({});
    setSearchParams({}, { replace: true });
  }, [applications, searchParams, setSearchParams]);
  async function submit(data) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiRequest(selected ? `/interviews/${selected.id}` : "/interviews", {
        method: selected ? "PUT" : "POST",
        body: data
      });
      setSelected(null);
      setPrefillId("");
      setNotice(selected ? "Interview updated." : "Interview scheduled.");
      toast.success(selected ? "Interview updated and saved." : "Interview scheduled and added to Your Schedule.");
      load();
      emitDataChanged("schedule");
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function setStatus(interview, status) {
    setError("");
    setNotice("");
    try {
      const { id, candidate_name, candidate_email, job_title, ...payload } = interview;
      await apiRequest(`/interviews/${id}`, { method: "PUT", body: { ...payload, status } });
      setNotice(`Interview marked ${status}.`);
      toast.success(`Interview marked ${status}. The candidate's status was updated.`);
      load();
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    }
  }
  async function setOutcome(interview) {
    setError("");
    setNotice("");
    try {
      const updated = await apiRequest(`/interviews/${interview.id}/outcome`, {
        method: "PATCH",
        body: { outcome: outcomeDrafts[interview.id] || interview.outcome, evaluation_notes: interview.evaluation_notes || "" }
      });
      setNotice(`${updated.round_name || "Interview"} outcome recorded as ${updated.outcome.replaceAll("_", " ")}.`);
      toast.success(`Outcome saved: ${updated.outcome.replaceAll("_", " ")}. ${updated.outcome === "passed" ? "This candidate can now be scheduled for the next round." : ""}`);
      load();
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    }
  }
  const ordered = [...interviews].sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at));
  function updateConfiguredRounds(jobId, rounds) {
    setJobs(current => current.map(job => job.id === jobId ? { ...job, interview_rounds: rounds } : job));
  }
  return (
    <div>
      <div className="page-heading"><div><div className="eyebrow">MAKE TIME FOR PEOPLE</div><h1>Interviews<span className="heading-period">.</span></h1><p className="muted">A clear schedule for every candidate conversation.</p></div>{!selected && <button className="button button-primary" onClick={() => setSelected({})}>＋ Schedule interview</button>}</div>
      <ErrorMessage>{error}</ErrorMessage>
      <InterviewSchedulingAssistant jobs={jobs} onBooked={load} />
      <InterviewRoundEditor jobs={jobs} onSaved={updateConfiguredRounds} />
      {selected && <section className="panel schedule-panel"><div className="panel-heading"><div><div className="eyebrow">{selected.id ? "UPDATE THE PLAN" : "PLAN A CONVERSATION"}</div><h2>{selected.id ? "Edit interview" : "Schedule an interview"}</h2></div></div><InterviewForm key={selected.id || prefillId || "new"} applications={applications} initial={selected.id ? selected : prefillId ? { application_id: prefillId } : null} busy={busy} onSubmit={submit} onCancel={() => { setSelected(null); setPrefillId(""); }} /></section>}
      <section className="panel list-panel interview-schedule"><div className="panel-heading"><div><div className="eyebrow">YOUR SCHEDULE</div><h2>All interviews <span className="count-pill">{interviews.length}</span></h2></div><span className="subtle-label live-label"><i className="live-dot" /> Live · saved data, refreshed automatically</span></div>
        {loading ? <div className="loading-row"><span className="spinner" />Loading interviews…</div> : !ordered.length ? <EmptyState title="No interviews scheduled">Plan a round above or ask the assistant to schedule one. Confirmed interviews appear here automatically.</EmptyState> : <div className="interview-list">{ordered.map(item => { const when = formatWhen(item); return <article className={`schedule-row ${item.status !== "scheduled" ? "schedule-row-muted" : ""}`} key={item.id}><div className="date-tile"><strong>{new Date(item.starts_at).toLocaleDateString(undefined, { day: "2-digit", timeZone: item.timezone })}</strong><small>{new Date(item.starts_at).toLocaleDateString(undefined, { month: "short", timeZone: item.timezone })}</small></div><div className="interview-person"><b>{item.candidate_name}</b><span>{item.job_title} · {item.round_name || item.interview_type}</span><dl className="schedule-facts"><div><dt>Date</dt><dd>{when.date}</dd></div><div><dt>Time</dt><dd>{when.time} ({item.timezone})</dd></div><div><dt>Interviewer</dt><dd>{item.interviewers.join(", ")}</dd></div>{(item.meeting_url || item.location) && <div><dt>Meeting</dt><dd>{item.meeting_url ? <a className="text-link" href={item.meeting_url} target="_blank" rel="noreferrer">Join link (temporary)</a> : item.location}</dd></div>}</dl><div className="schedule-tags">{item.status === "scheduled" && item.meeting_status && item.meeting_status !== "created" && <span className={`badge meeting-${item.meeting_status}`} title={item.meeting_error || ""}>{item.meeting_status === "not_configured" ? "No meeting provider" : "Meeting link unavailable"}</span>}{item.pipeline_status && <span className="pipeline-chip">{item.pipeline_status}</span>}{item.email_status && <span className={`badge email-${item.email_status}`}>{item.email_status === "sent" ? "Emails sent" : item.email_status === "not_configured" ? "Email not configured" : item.email_status === "partial" ? "Some emails pending" : "Email failed"}</span>}</div>{item.status === "completed" && <div className="outcome-editor"><label>Outcome<select value={outcomeDrafts[item.id] || item.outcome} onChange={event => setOutcomeDrafts(current => ({ ...current, [item.id]: event.target.value }))}><option value="pending_evaluation">Pending evaluation</option><option value="passed">Passed</option><option value="failed">Failed</option><option value="on_hold">On hold</option></select></label><button className="button button-quiet button-small" onClick={() => setOutcome(item)}>Record outcome</button></div>}</div><span className={`badge status-${item.status}`}>{item.status}</span><div className="schedule-actions"><button className="button button-quiet button-small" onClick={() => setSelected(item)}>Edit</button>{item.status === "scheduled" && <><button className="button button-quiet button-small" onClick={() => setStatus(item, "completed")}>Complete</button><button className="button button-quiet button-small danger-text" onClick={() => { if (window.confirm(`Cancel ${item.round_name || "this interview"} for ${item.candidate_name}? Their calendar event will be updated.`)) setStatus(item, "cancelled"); }}>Cancel</button></>}</div></article>; })}</div>}
      </section>
    </div>
  );
}
