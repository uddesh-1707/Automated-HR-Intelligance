import { useState } from "react";
import { apiRequest } from "../services/api";
import { ErrorMessage } from "./Feedback";
import DateRangeCalendar, { formatDateKey } from "./DateRangeCalendar";
import PlanNotifications from "./PlanNotifications";
import { useToast } from "./Toast";
import { emitDataChanged } from "../services/events";

const SCHEDULE_VERB = /\b(schedule|reschedule|book)\b/i;

export default function InterviewSchedulingAssistant({ jobs, onBooked }) {
  const [jobId, setJobId] = useState("");
  const [roundId, setRoundId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [choosingDates, setChoosingDates] = useState(false);
  const [range, setRange] = useState({ start: null, end: null });
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [calendarStatus, setCalendarStatus] = useState(null);
  const [checkingCalendar, setCheckingCalendar] = useState(false);
  const toast = useToast();

  const job = jobs.find(item => item.id === jobId);
  const configuredRounds = [...(job?.interview_rounds || [])].sort((a, b) => a.order - b.order);
  const round = configuredRounds.find(item => item.round_id === roundId);

  function effectiveInstruction() {
    const typed = instruction.trim();
    if (!typed) return `Schedule ${round?.name || "the next interview round"}`;
    return SCHEDULE_VERB.test(typed) ? typed : `Schedule ${round?.name || "interviews"}: ${typed}`;
  }

  function changeJob(value) {
    setJobId(value);
    setRoundId("");
    setChoosingDates(false);
  }

  function prepare(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    setChoosingDates(true);
  }

  async function generate(dates) {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const body = { instruction: effectiveInstruction(), job_id: jobId || null, round_id: roundId || null };
      if (dates) {
        body.window_start_date = dates.start;
        body.window_end_date = dates.end || dates.start;
      }
      setResult(await apiRequest("/interviews/assistant", { method: "POST", body }));
      setChoosingDates(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmPlan() {
    if (!result?.plan_id || !window.confirm("Recheck availability, save this schedule and email the candidates and interviewers? Candidates move to \u201cShortlisted\u201d for this round.")) return;
    setBusy(true);
    setError("");
    try {
      const confirmed = await apiRequest(`/interviews/scheduling-plans/${result.plan_id}/confirm`, { method: "POST" });
      setResult(current => ({ ...current, ...confirmed }));
      onBooked();
      emitDataChanged("schedule");
      const booked = confirmed.counts?.scheduled || 0;
      if (booked) toast.success(`${booked} interview${booked === 1 ? " is" : "s are"} booked. Candidate status updated and Your Schedule refreshed.`);
      else toast.error("No interview could be booked. See the reasons next to each candidate.");
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resendNotifications() {
    setBusy(true);
    setError("");
    try {
      const updated = await apiRequest(`/interviews/scheduling-plans/${result.plan_id}/resend-notifications`, { method: "POST" });
      setResult(current => ({ ...current, ...updated }));
      onBooked();
      toast.success("Email retry finished. Messages that were already delivered were not sent again.");
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function checkCalendar() {
    setCheckingCalendar(true);
    setError("");
    try {
      setCalendarStatus(await apiRequest(`/interviews/calendar-status${jobId ? `?job_id=${jobId}` : ""}`));
    } catch (err) {
      setError(err.message);
    } finally {
      setCheckingCalendar(false);
    }
  }

  const counts = result?.counts;
  return (
    <section className="panel scheduling-assistant">
      <div className="panel-heading"><div><div className="eyebrow">HR-DIRECTED, OUTCOME-GATED</div><h2>Plan interview rounds</h2><p className="muted">Pick a job and one of its configured rounds, then choose the interview dates on the calendar. A plan never books until you confirm it.</p></div><button type="button" className="button button-quiet button-small" onClick={checkCalendar} disabled={checkingCalendar}>{checkingCalendar ? "Checking…" : "Check availability sources"}</button></div>
      <form className="schedule-instruction-form" onSubmit={prepare}>
        <label>Job context<select value={jobId} onChange={event => changeJob(event.target.value)}><option value="">Infer from instruction</option>{jobs.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <label>Interview round<select aria-label="Interview round" value={roundId} disabled={!jobId} onChange={event => setRoundId(event.target.value)}><option value="">{!jobId ? "Select a job to list its rounds" : configuredRounds.length ? "Infer from instruction" : "No rounds configured yet"}</option>{configuredRounds.map(item => <option key={item.round_id} value={item.round_id}>{item.order}. {item.name} ({item.duration_minutes} min)</option>)}</select></label>
        <label className="schedule-instruction-wide">Scheduling instruction {round && <small>(optional — {round.name} is selected)</small>}<textarea required={!round} maxLength="1000" rows="3" value={instruction} onChange={event => setInstruction(event.target.value)} placeholder={'e.g. "Schedule tech 2 for Pallavi", "Schedule the interview of 5 candidates" or "Schedule Technical Interview 2 for candidates who passed Technical Interview 1."'} /></label>
        <button className="button button-primary" disabled={busy}>Prepare scheduling plan</button>
      </form>
      {choosingDates && <div className="date-picker-panel" role="region" aria-label="Choose interview dates">
        <div className="eyebrow">STEP 2 · CHOOSE DATES</div>
        <h3>When should these interviews happen?</h3>
        <p className="muted">Interviews are placed on the first free slot inside each round's working hours{job?.timezone ? ` (${job.timezone})` : ""}, avoiding interviewer calendars and existing bookings.</p>
        <DateRangeCalendar value={range} onChange={setRange} />
        <div className="date-picker-actions">
          <button type="button" className="button button-primary" disabled={busy || !range.start} onClick={() => generate(range)}>{busy ? "Checking calendars…" : range.start ? `Generate plan for ${formatDateKey(range.start)}${range.end && range.end !== range.start ? ` – ${formatDateKey(range.end)}` : ""}` : "Select a date to continue"}</button>
          <button type="button" className="button button-quiet" disabled={busy} onClick={() => generate(null)}>Use dates from my instruction instead</button>
          <button type="button" className="button button-quiet" disabled={busy} onClick={() => setChoosingDates(false)}>Cancel</button>
        </div>
      </div>}
      <div className="availability-policy">The scheduler checks HRMS bookings and, when a read-only Google Calendar is connected, the interviewers' calendars. Candidate calendars are not checked. Confirmed interviews are sent by email with a unique evaluation link for the interviewer; no calendar invitations are created.</div>
      <ErrorMessage>{error}</ErrorMessage>
      {calendarStatus && <CalendarStatus status={calendarStatus} onClose={() => setCalendarStatus(null)} />}
      {result && <div className="plan-result">
        {result.status === "clarification_required" ? <div className="alert alert-info" role="status">{result.clarifying_question}{result.matching_jobs?.length > 0 && <div className="assistant-job-choices">{result.matching_jobs.map(item => <button key={item.id} type="button" className="button button-quiet button-small" onClick={() => changeJob(item.id)}>{item.title}</button>)}</div>}</div> : <>
          {result.message && <p className="muted">{result.message}</p>}
          {result.round && <p className="plan-summary-line"><b>{result.job_title} · {result.round.name}</b>{result.round_inferred ? " (round chosen automatically — you did not name one)" : ""}{result.requested_count ? ` · limited to ${result.requested_count} candidate${result.requested_count === 1 ? "" : "s"}` : ""}{result.window_start ? ` · ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: result.timezone }).format(new Date(result.window_start))} – ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: result.timezone }).format(new Date(result.window_end))}` : ""}</p>}
          {result.availability_notice && <div className="alert alert-info" role="status">{result.availability_notice}</div>}
                    {result.calendar_warning && <div className="alert alert-error calendar-warning" role="alert"><div><b>Google Calendar problem</b><p>{result.calendar_warning}</p><button type="button" className="button button-quiet button-small" onClick={checkCalendar}>Diagnose calendar setup</button></div></div>}
          {counts && <div className="plan-counts">{[["Eligible", counts.eligible], ["Scheduled", counts.scheduled], ["Awaiting confirmation", counts.awaiting_confirmation], ["Awaiting evaluation", counts.awaiting_evaluation], ["Unable to schedule", counts.unable_to_schedule]].map(([label, value]) => <div key={label}><b>{value}</b><small>{label}</small></div>)}</div>}
          {result.items?.length > 0 && <div className="plan-candidate-list">{result.items.map(item => <div className="plan-candidate" key={item.application_id}><div><b>{item.candidate_name}</b><small>{item.candidate_email}</small></div><span className={`badge status-${item.status}`}>{item.status.replaceAll("_", " ")}</span><small>{item.starts_at ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: result.timezone }).format(new Date(item.starts_at)) : item.reason && item.reason === result.calendar_warning ? "Calendar unavailable — see the problem above" : item.reason}{item.email_status ? ` · email ${item.email_status.replaceAll("_", " ")}` : ""}{item.progression_override_reason ? ` · HR override: ${item.progression_override_reason}` : ""}</small></div>)}</div>}
          <PlanNotifications notifications={result.notifications} busy={busy} onResend={resendNotifications} />
          {result.status === "awaiting_confirmation" && <div className="plan-confirm-row"><p>Review the proposed slots above. Confirming saves the schedule, marks selected candidates as shortlisted for this round, and emails them and their interviewers. Availability is checked again first.</p><button className="button button-primary" disabled={busy || !counts?.awaiting_confirmation} onClick={confirmPlan}>{busy ? "Confirming…" : "Confirm & create calendar events"}</button></div>}
          {result.status === "confirmed" || result.status === "partially_confirmed" ? <div className="alert alert-success" role="status">Booking attempt complete. Review each result and reason above; no failed or pending-evaluation candidate was advanced.</div> : null}
        </>}
      </div>}
    </section>
  );
}

function CalendarStatus({ status, onClose }) {
  return (
    <div className={`calendar-status alert ${status.ok ? "alert-success" : "alert-error"}`} role="status">
      <div>
        <b>{status.ok ? "Google Calendar is connected" : "Google Calendar needs attention"}</b>
        {status.problem && <p>{status.problem}</p>}
        {status.service_account_email && <p>Share each calendar with this service account (permission “Make changes to events”): <code>{status.service_account_email}</code></p>}
        {status.checks?.length > 0 && <ul className="calendar-check-list">{status.checks.map(check => <li key={check.calendar_id}><span aria-hidden="true">{check.ok ? "✓" : "✗"}</span><div><b>{check.calendar_id}</b><small>{check.role}</small>{check.problem && <small>{check.problem}</small>}</div></li>)}</ul>}
        {status.steps?.length > 0 && <ol className="calendar-steps">{status.steps.map(step => <li key={step}>{step}</li>)}</ol>}
      </div>
      <button type="button" className="button button-quiet button-small" onClick={onClose}>Dismiss</button>
    </div>
  );
}
