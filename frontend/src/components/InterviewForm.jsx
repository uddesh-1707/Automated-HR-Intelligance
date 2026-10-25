import { useState } from "react";

const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function localDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export default function InterviewForm({ applications, initial, onSubmit, onCancel, busy }) {
  const [form, setForm] = useState(() => ({
    application_id: initial?.application_id || applications[0]?.id || "",
    starts_at: localDateTime(initial?.starts_at),
    timezone: initial?.timezone || browserZone,
    interview_type: initial?.interview_type || "video",
    interviewers: initial?.interviewers?.join(", ") || "",
    interviewer_emails: initial?.interviewer_emails?.join(", ") || "",
    round_id: initial?.round_id || "",
    round_name: initial?.round_name || "",
    duration_minutes: initial?.duration_minutes || 60,
    location: initial?.location || "",
    notes: initial?.notes || "",
    status: initial?.status || "scheduled",
    outcome: initial?.outcome || "pending_evaluation",
    evaluation_notes: initial?.evaluation_notes || ""
  }));
  function change(event) {
    const { name, value } = event.target;
    if (name === "round_id") {
      const application = applications.find(item => item.id === form.application_id);
      const round = application?.job?.interview_rounds?.find(item => item.round_id === value);
      setForm(current => ({
        ...current,
        round_id: value,
        round_name: round?.name || "",
        duration_minutes: round?.duration_minutes || 60,
        interview_type: round?.interview_type || current.interview_type,
        interviewers: round?.required_interviewers?.join(", ") || current.interviewers,
        interviewer_emails: round?.required_interviewers?.join(", ") || current.interviewer_emails,
        location: round?.location || current.location
      }));
      return;
    }
    setForm(current => ({ ...current, [name]: value }));
  }
  function submit(event) {
    event.preventDefault();
    const localTime = new Date(form.starts_at);
    if (Number.isNaN(localTime.getTime())) return;
    onSubmit({
      ...form,
      starts_at: localTime.toISOString(),
      interviewers: form.interviewers.split(",").map(item => item.trim()).filter(Boolean),
      interviewer_emails: form.interviewer_emails.split(",").map(item => item.trim()).filter(Boolean),
      round_name: form.round_name || null,
      round_id: form.round_id || null,
      duration_minutes: Number(form.duration_minutes)
    });
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="full">Candidate application<select required name="application_id" value={form.application_id} onChange={change}><option value="">Choose an application</option>{applications.map(item => <option key={item.id} value={item.id}>{item.candidate.name} · {item.job.title}</option>)}</select></label>
      <label className="full">Interview round<select name="round_id" value={form.round_id} onChange={change}><option value="">Manual / unconfigured round</option>{applications.find(item => item.id === form.application_id)?.job?.interview_rounds?.map(round => <option key={round.round_id} value={round.round_id}>{round.order}. {round.name}</option>)}</select></label>
      <label>Date and time<input required type="datetime-local" name="starts_at" value={form.starts_at} onChange={change} /></label>
      <label>Timezone<input required name="timezone" value={form.timezone} readOnly title="Detected from this device" /></label>
      <label>Interview type<select name="interview_type" value={form.interview_type} onChange={change}><option value="video">Video call</option><option value="phone">Phone</option><option value="onsite">On-site</option><option value="technical">Technical</option><option value="other">Other</option></select></label>
      <label>Duration (minutes)<input required type="number" min="15" max="240" step="15" name="duration_minutes" value={form.duration_minutes} onChange={change} /></label>
      <label>Interviewer(s)<input required name="interviewers" placeholder="Name, Name" value={form.interviewers} onChange={change} /></label>
      <label>Interviewer calendar emails<input name="interviewer_emails" placeholder="person@company.com" value={form.interviewer_emails} onChange={change} /></label>
      <label className="full">{form.interview_type === "onsite" ? "Location" : "Meeting link or location"}<input required={["onsite", "video"].includes(form.interview_type)} name="location" placeholder={form.interview_type === "onsite" ? "Office address or room" : "https://…"} value={form.location} onChange={change} /></label>
      <label className="full">Notes<textarea name="notes" rows="3" value={form.notes} onChange={change} /></label>
      {initial && <label>Status<select name="status" value={form.status} onChange={change}><option value="scheduled">Scheduled</option><option value="completed">Completed</option><option value="cancelled">Cancelled</option></select></label>}
      {form.status === "completed" && <><label>Outcome<select name="outcome" value={form.outcome} onChange={change}><option value="pending_evaluation">Pending evaluation</option><option value="passed">Passed</option><option value="failed">Failed</option><option value="on_hold">On hold</option></select></label><label className="full">Evaluation notes<textarea rows="3" maxLength="5000" name="evaluation_notes" value={form.evaluation_notes} onChange={change} /></label></>}
      <div className="form-actions full"><button type="button" className="button button-quiet" onClick={onCancel}>Cancel</button><button className="button button-primary" disabled={busy}>{busy ? "Saving…" : initial ? "Save changes" : "Schedule interview"}</button></div>
    </form>
  );
}
