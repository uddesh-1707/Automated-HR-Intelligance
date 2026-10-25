import { useEffect, useRef, useState } from "react";
import { apiRequest } from "../services/api";
import { ErrorMessage } from "./Feedback";

function newRound(order) {
  return {
    round_id: crypto.randomUUID().replaceAll("-", ""),
    name: "",
    order,
    duration_minutes: 60,
    interview_type: "technical",
    required_interviewers: "",
    requires_hr: false,
    prerequisite_round_ids: [],
    location: "",
    working_day_start_hour: 9,
    working_day_end_hour: 17,
    slot_interval_minutes: 30
  };
}

function toDraft(item) {
  return {
    ...item,
    required_interviewers: item.required_interviewers.join(", "),
    prerequisite_round_ids: [...item.prerequisite_round_ids]
  };
}

function signature(round) {
  const interviewers = Array.isArray(round.required_interviewers)
    ? round.required_interviewers
    : String(round.required_interviewers).split(",").map(item => item.trim()).filter(Boolean);
  return JSON.stringify([
    round.name, Number(round.order), Number(round.duration_minutes), round.interview_type, interviewers,
    Boolean(round.requires_hr), [...round.prerequisite_round_ids].sort(), round.location || "",
    Number(round.working_day_start_hour), Number(round.working_day_end_hour), Number(round.slot_interval_minutes)
  ]);
}

function diffRounds(saved, draft) {
  const savedById = new Map(saved.map(item => [item.round_id, item]));
  const draftIds = new Set(draft.map(item => item.round_id));
  return {
    added: draft.filter(item => !savedById.has(item.round_id)),
    removed: saved.filter(item => !draftIds.has(item.round_id)),
    changed: draft.filter(item => savedById.has(item.round_id) && signature(savedById.get(item.round_id)) !== signature(item))
  };
}

function describeDiff({ added, removed, changed }) {
  const parts = [];
  if (added.length) parts.push(`${added.length} added (${added.map(item => item.name || "unnamed").join(", ")})`);
  if (changed.length) parts.push(`${changed.length} updated (${changed.map(item => item.name || "unnamed").join(", ")})`);
  if (removed.length) parts.push(`${removed.length} removed (${removed.map(item => item.name).join(", ")})`);
  return parts.join(" · ");
}

function RoundSummary({ rounds }) {
  const names = new Map(rounds.map(item => [item.round_id, item.name]));
  return <ol className="round-summary-list">{[...rounds].sort((a, b) => a.order - b.order).map(round => <li key={round.round_id}>
    <b>Round {round.order} · {round.name}</b>
    <span>{round.duration_minutes} min · {round.interview_type} · {round.working_day_start_hour}:00–{round.working_day_end_hour}:00{round.requires_hr ? " · HR attends" : ""}</span>
    <small>Interviewers: {round.required_interviewers.join(", ")}</small>
    {round.prerequisite_round_ids.length > 0 && <small>Requires a Passed outcome in: {round.prerequisite_round_ids.map(id => names.get(id) || "a removed round").join(", ")}</small>}
  </li>)}</ol>;
}

const draftKey = jobId => `hrms_round_draft_${jobId}`;

function readStoredDraft(jobId) {
  try {
    const stored = sessionStorage.getItem(draftKey(jobId));
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

function writeStoredDraft(jobId, rounds) {
  try {
    if (rounds) sessionStorage.setItem(draftKey(jobId), JSON.stringify(rounds));
    else sessionStorage.removeItem(draftKey(jobId));
  } catch { /* storage unavailable: the in-memory draft still works */ }
}

export default function InterviewRoundEditor({ jobs, onSaved }) {
  const [jobId, setJobId] = useState("");
  const [rounds, setRounds] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const job = jobs.find(item => item.id === jobId);
  const savedRounds = job?.interview_rounds || [];

  const pending = diffRounds(savedRounds, rounds);
  const hasUnsaved = pending.added.length + pending.removed.length + pending.changed.length > 0;
  const dirtyRef = useRef(false);
  dirtyRef.current = hasUnsaved;
  const savedKey = JSON.stringify(savedRounds);
  const loaded = useRef({ jobId: "", key: "" });

  // The draft is (re)loaded from the saved rounds only when a different job is chosen, or when the saved rounds
  // really changed while there are no unsaved edits. The parent refreshes `jobs` every minute, on tab focus and
  // after bookings, producing a new array each time; resetting on that identity change was wiping the form.
  useEffect(() => {
    const jobChanged = loaded.current.jobId !== jobId;
    if (jobChanged) {
      setRounds(readStoredDraft(jobId) || savedRounds.map(toDraft));
      setError("");
      setNotice("");
    } else if (loaded.current.key !== savedKey && !dirtyRef.current) {
      setRounds(savedRounds.map(toDraft));
    }
    loaded.current = { jobId, key: savedKey };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, savedKey]);

  // Keep unsaved edits across accidental remounts and page refreshes until saved or discarded.
  useEffect(() => {
    if (jobId && loaded.current.jobId === jobId) writeStoredDraft(jobId, hasUnsaved ? rounds : null);
  }, [rounds, hasUnsaved, jobId]);

  function chooseJob(nextJobId) {
    if (hasUnsaved && !window.confirm("You have unsaved round changes for this job. Switch jobs anyway? Your draft is kept and restored when you come back.")) return;
    setJobId(nextJobId);
  }

  function discardChanges() {
    if (!window.confirm("Discard all unsaved round changes for this job?")) return;
    writeStoredDraft(jobId, null);
    setRounds(savedRounds.map(toDraft));
    setError("");
  }

  function changeRound(roundId, field, value) {
    setRounds(current => {
      const updated = current.map(round => round.round_id === roundId ? { ...round, [field]: value } : round);
      if (field !== "order") return updated;
      // A prerequisite must come earlier; keep the user's other input and only prune links the new order invalidates.
      const orderOf = new Map(updated.map(round => [round.round_id, Number(round.order)]));
      return updated.map(round => ({
        ...round,
        prerequisite_round_ids: round.prerequisite_round_ids.filter(id => orderOf.has(id) && orderOf.get(id) < Number(round.order))
      }));
    });
  }

  function removeRound(roundId) {
    // Also drop prerequisites that pointed at the removed round, so saving does not fail validation.
    setRounds(current => current
      .filter(item => item.round_id !== roundId)
      .map(item => ({ ...item, prerequisite_round_ids: item.prerequisite_round_ids.filter(id => id !== roundId) })));
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const configured = rounds.map(round => ({
        ...round,
        order: Number(round.order),
        duration_minutes: Number(round.duration_minutes),
        working_day_start_hour: Number(round.working_day_start_hour),
        working_day_end_hour: Number(round.working_day_end_hour),
        slot_interval_minutes: Number(round.slot_interval_minutes),
        required_interviewers: round.required_interviewers.split(",").map(item => item.trim()).filter(Boolean)
      }));
      const summary = describeDiff(diffRounds(savedRounds, configured));
      const result = await apiRequest(`/interviews/jobs/${jobId}/rounds`, { method: "PUT", body: { rounds: configured } });
      writeStoredDraft(jobId, null);
      setRounds(result.rounds.map(toDraft));
      onSaved(jobId, result.rounds);
      setNotice(`Saved ${result.rounds.length} interview round${result.rounds.length === 1 ? "" : "s"} for "${job?.title}"${summary ? ` — ${summary}` : " — no changes"}. They are now available when planning interviews.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel round-config-panel">
      <div className="panel-heading"><div><div className="eyebrow">JOB-SPECIFIC WORKFLOW</div><h2>Configure interview rounds</h2><p className="muted">Set prerequisites, interviewers, duration, and working hours for each job.</p></div></div>
      <label className="round-job-select">Job<select value={jobId} onChange={event => chooseJob(event.target.value)}><option value="">Choose a job</option>{jobs.map(job => <option key={job.id} value={job.id}>{job.title}</option>)}</select></label>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {notice && <div className="alert alert-success" role="status"><span>✓</span>{notice}</div>}
      {!jobId ? <p className="muted">Choose a job to configure its interview rounds.</p> : <form onSubmit={save}>
        <div className="round-saved-summary"><div className="eyebrow">ALREADY CONFIGURED FOR THIS JOB · {savedRounds.length}</div>{savedRounds.length ? <RoundSummary rounds={savedRounds} /> : <p className="muted">No rounds saved yet. Add a round below, then save.</p>}</div>
        {hasUnsaved && <div className="alert alert-info" role="status">Unsaved changes: {describeDiff(pending)}. Click “Save round configuration” to apply them.</div>}
        <div className="round-config-list">{rounds.map(round => <article className="round-config-card" key={round.round_id}>
          <div className="round-config-top"><b>Round {round.order || "—"}</b><button type="button" className="button button-quiet button-small danger-text" onClick={() => removeRound(round.round_id)}>Remove</button></div>
          <div className="round-fields">
            <label>Round name<input required maxLength="120" value={round.name} onChange={event => changeRound(round.round_id, "name", event.target.value)} placeholder="Technical Interview 1" /></label>
            <label>Order<input required type="number" min="1" max="50" value={round.order} onChange={event => changeRound(round.round_id, "order", event.target.value)} /></label>
            <label>Duration (minutes)<input required type="number" min="15" max="240" step="15" value={round.duration_minutes} onChange={event => changeRound(round.round_id, "duration_minutes", event.target.value)} /></label>
            <label>Interview type<select value={round.interview_type} onChange={event => changeRound(round.round_id, "interview_type", event.target.value)}><option value="technical">Technical</option><option value="phone">Phone</option><option value="video">Video</option><option value="onsite">On-site</option><option value="other">Other</option></select></label>
            <label className="round-wide">Required interviewer emails<input required value={round.required_interviewers} onChange={event => changeRound(round.round_id, "required_interviewers", event.target.value)} placeholder="interviewer@company.com, lead@company.com" /><small>Each calendar must be shared with the HRMS Google service account.</small></label>
            {["video", "onsite"].includes(round.interview_type) && <label className="round-wide">Meeting link or location<input required value={round.location || ""} onChange={event => changeRound(round.round_id, "location", event.target.value)} /></label>}
            <label>Working day starts<input required type="number" min="0" max="23" value={round.working_day_start_hour} onChange={event => changeRound(round.round_id, "working_day_start_hour", event.target.value)} /></label>
            <label>Working day ends<input required type="number" min="1" max="24" value={round.working_day_end_hour} onChange={event => changeRound(round.round_id, "working_day_end_hour", event.target.value)} /></label>
            <label>Slot interval (minutes)<input required type="number" min="15" max="120" step="15" value={round.slot_interval_minutes} onChange={event => changeRound(round.round_id, "slot_interval_minutes", event.target.value)} /></label>
            <label className="round-check"><input type="checkbox" checked={round.requires_hr} onChange={event => changeRound(round.round_id, "requires_hr", event.target.checked)} />HR attendance required</label>
            <fieldset className="round-wide prerequisite-options"><legend>Required previous rounds (must have a recorded Passed outcome)</legend>{rounds.filter(item => item.round_id !== round.round_id && Number(item.order) < Number(round.order)).map(previous => <label key={previous.round_id}><input type="checkbox" checked={round.prerequisite_round_ids.includes(previous.round_id)} onChange={event => {
              const values = event.target.checked
                ? [...round.prerequisite_round_ids, previous.round_id]
                : round.prerequisite_round_ids.filter(id => id !== previous.round_id);
              changeRound(round.round_id, "prerequisite_round_ids", values);
            }} />{previous.name || `Round ${previous.order}`}</label>)}</fieldset>
          </div>
        </article>)}</div>
        <div className="round-config-actions"><button type="button" className="button button-quiet" onClick={() => setRounds(current => [...current, newRound(Math.max(0, ...current.map(item => Number(item.order) || 0)) + 1)])}>＋ Add round</button>{hasUnsaved && <button type="button" className="button button-quiet" disabled={busy} onClick={discardChanges}>Discard changes</button>}<button type="submit" className="button button-primary" disabled={busy || !rounds.length || !hasUnsaved}>{busy ? "Saving…" : hasUnsaved ? "Save round configuration" : "All changes saved"}</button></div>
      </form>}
    </section>
  );
}
