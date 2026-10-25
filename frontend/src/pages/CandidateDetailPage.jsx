import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiRequest } from "../services/api";
import { ErrorMessage, StageBadge } from "../components/Feedback";

const stages = ["needs_review", "screening", "interview", "offer", "hired", "rejected"];

export default function CandidateDetailPage() {
  const { applicationId } = useParams();
  const [application, setApplication] = useState(null);
  const [interviews, setInterviews] = useState([]);
  const [progressionOverrides, setProgressionOverrides] = useState([]);
  const [overrideRoundId, setOverrideRoundId] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideAuthorized, setOverrideAuthorized] = useState(false);
  const [form, setForm] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [emailType, setEmailType] = useState("shortlist");
  const [emailMessage, setEmailMessage] = useState("");
  const [emailDraft, setEmailDraft] = useState(null);
  const [emailDrafts, setEmailDrafts] = useState([]);
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    Promise.all([
      apiRequest(`/recruitment/applications/${applicationId}`),
      apiRequest(`/interviews/applications/${applicationId}`),
      apiRequest(`/interviews/applications/${applicationId}/progression-overrides`),
      apiRequest(`/recruitment/applications/${applicationId}/email-drafts`)
    ])
      .then(([result, scheduled, overrides, drafts]) => {
        setApplication(result);
        setInterviews(scheduled);
        setProgressionOverrides(overrides);
        setEmailDrafts(drafts);
        setForm({ ...result.candidate, skills: result.candidate.skills.join(", "), stage: result.stage, hr_notes: result.hr_notes || "" });
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [applicationId]);
  function change(event) {
    setForm(current => ({ ...current, [event.target.name]: event.target.value }));
  }
  async function save(event, stageOverride) {
    event?.preventDefault();
    if (!form) return;
    setBusy(true);
    setError("");
    const payload = {
      ...form,
      skills: form.skills.split(",").map(skill => skill.trim()).filter(Boolean),
      experience_years: form.experience_years === "" || form.experience_years == null ? null : Number(form.experience_years),
      stage: stageOverride || form.stage,
      hr_notes: form.hr_notes || ""
    };
    try {
      const result = await apiRequest(`/recruitment/applications/${applicationId}`, { method: "PATCH", body: payload });
      setApplication(result);
      setForm({ ...result.candidate, skills: result.candidate.skills.join(", "), stage: result.stage, hr_notes: result.hr_notes || "" });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function rerunMatching() {
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/recruitment/applications/${applicationId}/rerun-matching`, { method: "POST" });
      setApplication(current => ({ ...current, matching_status: "pending", processing_status: "pending", ats_score: null }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function createEmailDraft(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await apiRequest(`/recruitment/applications/${applicationId}/email-drafts`, {
        method: "POST",
        body: { email_type: emailType, message: emailMessage }
      });
      setEmailDraft(created);
      setEmailSubject(created.subject);
      setEmailBody(created.body);
      setEmailDrafts(current => [created, ...current.filter(item => item._id !== created._id)]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function approveEmail() {
    if (!emailDraft || !window.confirm(`Approve and send this email to ${emailDraft.recipient}?`)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await apiRequest(`/recruitment/email-drafts/${emailDraft._id}/approve-and-send`, { method: "POST" });
      setNotice(result.delivery_status === "sent" ? "Email was accepted by the configured SMTP server." : `Email status: ${result.delivery_status}`);
      setEmailDrafts(current => current.map(item => item._id === emailDraft._id ? { ...item, ...result } : item));
      setEmailDraft(null);
    } catch (err) {
      setError(err.message);
      try {
        const status = await apiRequest(`/recruitment/email-drafts/${emailDraft._id}/status`);
        setEmailDraft(current => current?._id === emailDraft._id ? { ...current, ...status } : current);
        setEmailDrafts(current => current.map(item => item._id === emailDraft._id ? { ...item, ...status } : item));
      } catch (statusError) {
        setEmailDraft(current => current?._id === emailDraft._id ? { ...current, delivery_status: "unknown" } : current);
        setEmailDrafts(current => current.map(item => item._id === emailDraft._id ? { ...item, delivery_status: "unknown" } : item));
        setError(`${err.message} Delivery status could not be verified: ${statusError.message}`);
      }
    } finally {
      setBusy(false);
    }
  }
  async function saveEmailDraft(event) {
    event.preventDefault();
    if (!emailDraft) return;
    setBusy(true);
    setError("");
    try {
      const updated = await apiRequest(`/recruitment/email-drafts/${emailDraft._id}`, {
        method: "PATCH",
        body: { subject: emailSubject, body: emailBody }
      });
      setEmailDraft(updated);
      setEmailDrafts(current => current.map(item => item._id === updated._id ? updated : item));
      setNotice("Email draft saved.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function deleteEmailDraft(draft) {
    if (!window.confirm(`Delete the unsent draft to ${draft.recipient}?`)) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/recruitment/email-drafts/${draft._id}`, { method: "DELETE" });
      setEmailDrafts(current => current.filter(item => item._id !== draft._id));
      if (emailDraft?._id === draft._id) setEmailDraft(null);
      setNotice("Unsent email draft deleted.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function prepareEmailRetry(draft) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const retry = await apiRequest(`/recruitment/email-drafts/${draft._id}/retry`, { method: "POST" });
      setEmailDraft(retry);
      setEmailSubject(retry.subject);
      setEmailBody(retry.body);
      setEmailDrafts(current => [retry, ...current.filter(item => item._id !== retry._id)]);
      setNotice("The provider explicitly rejected the earlier attempt. Review and confirm this new attempt.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  function editEmailDraft(draft) {
    setEmailDraft(draft);
    setEmailSubject(draft.subject);
    setEmailBody(draft.body);
  }
  async function authorizeOverride(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await apiRequest(`/interviews/applications/${applicationId}/progression-overrides`, {
        method: "POST",
        body: {
          round_id: overrideRoundId,
          reason: overrideReason,
          explicitly_authorized: overrideAuthorized
        }
      });
      setProgressionOverrides(current => [created, ...current]);
      setOverrideReason("");
      setOverrideAuthorized(false);
      setNotice("Progression override recorded. The original round outcome remains unchanged.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function revokeOverride(roundId) {
    if (!window.confirm("Revoke this progression override? The recorded interview outcome will not change.")) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/interviews/applications/${applicationId}/progression-overrides/${roundId}`, { method: "DELETE" });
      setProgressionOverrides(current => current.map(item => item.round_id === roundId ? { ...item, active: false } : item));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function downloadResume() {
    setError("");
    try {
      const blob = await apiRequest(`/recruitment/applications/${applicationId}/resume`, { responseType: "blob" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = application.resume_filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    }
  }
  if (loading) return <div className="loading-row"><span className="spinner" />Loading candidate…</div>;
  if (!application || !form) return <div><ErrorMessage>{error || "This application could not be found."}</ErrorMessage><Link to="/recruitment" className="text-link">← Back to recruitment</Link></div>;
  return (
    <div>
      <div className="back-link"><Link to="/recruitment">← Recruitment</Link><span>/</span>{application.job.title}</div>
      <div className="detail-heading"><div><div className="eyebrow">CANDIDATE REVIEW</div><h1>{application.candidate.name}</h1><p className="muted">{application.candidate.email} · Applied {new Date(application.applied_at).toLocaleDateString()}</p></div><div className="detail-head-actions"><StageBadge stage={application.stage} />{application.pipeline_status && <span className="pipeline-chip">{application.pipeline_status}</span>}<button onClick={downloadResume} className="button button-quiet">↓ Download resume</button></div></div>
      <ErrorMessage>{error}</ErrorMessage>
      {notice && <div className="alert alert-success" role="status">{notice}</div>}
      {application.stage === "needs_review" && <div className="review-note"><span>✦</span><div><b>Review before advancing</b><p>Resume parsing is a best-effort starting point. Confirm or correct every detail before moving this application into screening.</p></div></div>}
      <section className="panel ats-panel"><div className="panel-heading"><div><div className="eyebrow">DECISION SUPPORT · NOT AN AUTOMATIC DECISION</div><h2>Job-match assessment</h2></div><div className="assessment-actions"><span className={`badge status-${application.matching_status}`}>{application.matching_status}</span><button className="button button-quiet button-small" disabled={busy} onClick={rerunMatching}>{busy ? "Queueing…" : "↻ Re-run matching"}</button></div></div>
        {application.ats_score == null ? <p className="muted">{application.processing_error || "No ATS score is available yet. Matching requires configured embedding and Pinecone services."}</p> : <><div className="score-summary"><div className="score-number">{application.ats_score}<small>/100</small></div><div><b>Recommendation: HR review</b><p>This score summarizes job-relevant evidence; it is not a hiring decision. Mandatory criteria and source excerpts require human review.</p><span>Confidence: {application.confidence} · Scoring version: {application.scoring_version}</span></div></div><div className="criterion-grid">{Object.entries(application.criterion_scores || {}).map(([key, item]) => <div className="criterion-item" key={key}><span>{key.replaceAll("_", " ")}</span><b>{item.score}%</b><small>Weight {item.weight}% · weighted {item.weighted_score}</small></div>)}</div>{application.missing_criteria?.length > 0 && <div className="assessment-flags"><b>Missing or unverified</b>{application.missing_criteria.map(item => <span key={item}>• {item}</span>)}</div>}{application.mandatory_criteria_results?.length > 0 && <div className="assessment-flags"><b>Mandatory criteria · verify with HR</b>{application.mandatory_criteria_results.map(item => <span key={item.criterion}>• {item.criterion}: {item.status.replaceAll("_", " ")}</span>)}</div>}{application.matching_evidence?.length > 0 && <div className="evidence-list"><b>Retrieved resume evidence</b>{application.matching_evidence.map((item, index) => <blockquote key={`${item.criterion}-${index}`}><span>{item.criterion.replaceAll("_", " ")} · {item.section} · similarity {item.semantic_similarity}</span><p>“{item.excerpt}”</p><small>{item.explanation}</small></blockquote>)}</div>}</>}
      </section>
      {application.assessment && <section className="panel ats-panel"><div className="panel-heading"><div><div className="eyebrow">RESUME ASSESSMENT · EVIDENCE-BASED</div><h2>Fit to the job description</h2></div><span className={`badge status-${application.shortlist_status || application.assessment.eligibility}`}>{(application.shortlist_status || application.assessment.eligibility).replaceAll("_", " ")}{application.shortlist_rank ? ` #${application.shortlist_rank}` : ""}</span></div>
        <p className="muted">{application.assessment.explanation}</p>
        <div className="criterion-grid">{Object.entries(application.assessment.scores || {}).map(([key, value]) => <div className="criterion-item" key={key}><span>{key.replaceAll("_", " ")}</span><b>{value == null ? "no evidence yet" : `${value}%`}</b><small>{application.assessment.weights_applied?.[key] != null ? `Weight ${application.assessment.weights_applied[key]}%` : "Not scored"}</small></div>)}</div>
        <div className="evidence-list">{[...(application.assessment.required_skills || []), ...(application.assessment.preferred_skills || [])].map(item => <blockquote key={item.requirement}><span>{item.requirement} · {item.status.replaceAll("_", " ")}{item.method && item.method !== "none" ? ` · ${item.method}` : ""}</span>{item.evidence && <p>“{item.evidence}”</p>}</blockquote>)}</div>
        {application.assessment.parse_warnings?.length > 0 && <div className="assessment-flags"><b>Parsing problems</b>{application.assessment.parse_warnings.map(item => <span key={item}>• {item}</span>)}</div>}
      </section>}
      <section className="panel candidate-interview-history"><div className="panel-heading"><div><div className="eyebrow">ROUND HISTORY · RECORDED OUTCOMES</div><h2>Interview rounds</h2></div><Link to={`/interviews?application=${application.id}`} className="text-link">Manage interviews →</Link></div>
        {!interviews.length ? <p className="muted">No interview rounds have been scheduled for this candidate.</p> : <div className="candidate-round-list">{interviews.map(item => <article key={item.id}><div><b>{item.round_name || item.interview_type}</b><small>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: item.timezone }).format(new Date(item.starts_at))} · {item.timezone}</small></div><span className={`badge status-${item.status}`}>{item.status.replaceAll("_", " ")}</span><span className={`badge outcome-${item.outcome}`}>{item.outcome.replaceAll("_", " ")}</span>{item.evaluation_notes && <p>{item.evaluation_notes}</p>}</article>)}</div>}
      </section>
      {application.job.interview_rounds?.length > 0 && <section className="panel progression-override-panel"><div className="panel-heading"><div><div className="eyebrow">EXPLICIT HR EXCEPTION · AUDITED</div><h2>Progression overrides</h2><p className="muted">An override permits scheduling a selected round despite unmet or unsuccessful prerequisites. It never changes the recorded outcome or hiring stage.</p></div></div>
        {progressionOverrides.map(item => <div className="override-record" key={item.id}><div><b>{item.round_name}</b><small>{item.reason}</small></div><span className={`badge ${item.active ? "status-completed" : "status-cancelled"}`}>{item.active ? "active" : "revoked"}</span>{item.active && <button className="button button-quiet button-small danger-text" disabled={busy} onClick={() => revokeOverride(item.round_id)}>Revoke</button>}</div>)}
        {!["hired", "rejected"].includes(application.stage) && <form className="override-form" onSubmit={authorizeOverride}><label>Round<select required value={overrideRoundId} onChange={event => setOverrideRoundId(event.target.value)}><option value="">Select a round</option>{application.job.interview_rounds.map(round => <option key={round.round_id} value={round.round_id}>{round.order}. {round.name}</option>)}</select></label><label>Reason (required for audit)<textarea required minLength="10" maxLength="1000" rows="2" value={overrideReason} onChange={event => setOverrideReason(event.target.value)} /></label><label className="override-confirm"><input type="checkbox" required checked={overrideAuthorized} onChange={event => setOverrideAuthorized(event.target.checked)} />I explicitly authorize progression without treating any previous outcome as passed.</label><button className="button button-quiet" disabled={busy}>{busy ? "Recording…" : "Record authorized override"}</button></form>}
      </section>}
      <section className="panel email-panel"><div className="panel-heading"><div><div className="eyebrow">HR-APPROVED COMMUNICATION</div><h2>Recruitment emails</h2><p className="muted">Drafts are saved for HR review. Sending requires explicit approval and working SMTP configuration.</p></div></div>
        <div className="email-draft-history">{emailDrafts.map(draft => <article key={draft._id}><div><b>{draft.subject}</b><small>To {draft.recipient} · {draft.email_type.replaceAll("_", " ")} · {draft.delivery_status.replaceAll("_", " ")}</small>{draft.sent_at && <small>Sent {new Date(draft.sent_at).toLocaleString()}</small>}{draft.error_message && <small role="alert">{draft.error_message}</small>}</div>{draft.approval_status === "draft" && draft.delivery_status === "not_sent" ? <div className="email-actions"><button className="button button-quiet button-small" disabled={busy} onClick={() => editEmailDraft(draft)}>Edit</button><button className="button button-quiet button-small danger-text" disabled={busy} onClick={() => deleteEmailDraft(draft)}>Delete</button></div> : <div className="email-actions">{draft.delivery_failure_type === "explicit_rejection" && <button className="button button-quiet button-small" disabled={busy} onClick={() => prepareEmailRetry(draft)}>Prepare safe retry</button>}<button className="button button-quiet button-small" disabled={busy} onClick={() => editEmailDraft(draft)}>View status</button></div>}</article>)}</div>
        {!emailDraft ? <form className="email-draft-form" onSubmit={createEmailDraft}><label>Email type<select value={emailType} onChange={e => setEmailType(e.target.value)}><option value="shortlist">Shortlist / next steps</option><option value="interview_invitation">Scheduled interview invitation</option><option value="follow_up">Follow-up</option><option value="rejection">Application update</option></select></label><label>Optional note<textarea rows="3" maxLength="5000" value={emailMessage} onChange={e => setEmailMessage(e.target.value)} placeholder="Add only confirmed, job-relevant information" /></label><button className="button button-quiet" disabled={busy}>{busy ? "Preparing…" : "Prepare email draft"}</button></form> : <form className="email-preview" onSubmit={saveEmailDraft}><div className="metadata"><span>To</span><b>{emailDraft.recipient}</b></div><label>Subject<input required maxLength="200" value={emailSubject} onChange={event => setEmailSubject(event.target.value)} disabled={emailDraft.approval_status !== "draft" || emailDraft.delivery_status !== "not_sent"} /></label><label>Message<textarea required rows="8" maxLength="10000" value={emailBody} onChange={event => setEmailBody(event.target.value)} disabled={emailDraft.approval_status !== "draft" || emailDraft.delivery_status !== "not_sent"} /></label><div className="email-actions"><button type="button" className="button button-quiet" onClick={() => setEmailDraft(null)}>Close</button>{emailDraft.approval_status === "draft" && emailDraft.delivery_status === "not_sent" && <><button type="submit" className="button button-quiet" disabled={busy}>{busy ? "Saving…" : "Save changes"}</button><button type="button" className="button button-primary" disabled={busy} onClick={approveEmail}>{busy ? "Sending…" : "Approve & send"}</button></>}{emailDraft.approval_status === "approved" && ["not_configured", "not_sent"].includes(emailDraft.delivery_status) && <button type="button" className="button button-primary" disabled={busy} onClick={approveEmail}>{busy ? "Sending…" : emailDraft.retry_of ? "Confirm & send retry" : "Retry after SMTP setup"}</button>}</div>{emailDraft.error_message && <small role="alert">{emailDraft.error_message}</small>}<small>Only SMTP-accepted delivery is shown as sent. Ambiguous failures are not automatically retried.</small></form>}
      </section>
      <div className="detail-grid">
        <section className="panel detail-form-panel"><div className="panel-heading"><div><div className="eyebrow">PARSED CANDIDATE PROFILE</div><h2>Review details</h2></div><span className="subtle-label">Edit before saving</span></div>
          <form className="form-grid" onSubmit={e => save(e)}><label className="full">Full name<input required name="name" maxLength="180" value={form.name} onChange={change} /></label><label>Email address<input required type="email" name="email" value={form.email} onChange={change} /></label><label>Phone<input name="phone" value={form.phone || ""} onChange={change} /></label><label className="full">Skills <small>Separate with commas</small><input name="skills" value={form.skills} onChange={change} placeholder="Python, project management…" /></label><label>Experience (years)<input type="number" min="0" max="80" step="0.5" name="experience_years" value={form.experience_years ?? ""} onChange={change} /></label><label>Application stage<select name="stage" value={form.stage} onChange={change}>{stages.map(stage => <option key={stage} value={stage}>{stage.replaceAll("_", " ")}</option>)}</select></label><label className="full">Education<textarea rows="4" name="education" value={form.education || ""} onChange={change} /></label><label className="full">Internal HR notes<textarea rows="3" name="hr_notes" maxLength="10000" value={form.hr_notes || ""} onChange={change} placeholder="Visible only to your HR team" /></label><div className="form-actions full"><button className="button button-primary" disabled={busy}>{busy ? "Saving…" : "Save review"}</button>{application.stage === "needs_review" && <button type="button" className="button button-dark" disabled={busy} onClick={() => save(null, "screening")}>Confirm & move to screening →</button>}</div></form>
        </section>
        <aside className="detail-side"><section className="panel side-card"><div className="eyebrow">APPLICATION</div><h3>{application.job.title}</h3><p>{application.job.department || "Department not set"}</p><div className="side-divider" /><div className="metadata"><span>Submitted</span><b>{new Date(application.applied_at).toLocaleString()}</b></div><div className="metadata"><span>Resume</span><b>{application.resume_filename}</b></div><div className="metadata"><span>File size</span><b>{(application.resume_size / 1024 / 1024).toFixed(2)} MB</b></div></section>
          <section className="panel side-card resume-parse"><div className="metric-icon violet">✧</div><h3>Parsing, not guessing.</h3><p>Skills and experience are extracted from readable resume text. The candidate name and missing details are never inferred.</p></section>
          <Link to={`/interviews?application=${application.id}`} className="button button-primary button-wide">Schedule an interview <span>→</span></Link>
        </aside>
      </div>
    </div>
  );
}
