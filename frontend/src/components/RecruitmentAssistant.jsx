import { useState } from "react";
import { Link } from "react-router-dom";
import { apiRequest } from "../services/api";
import { ErrorMessage } from "./Feedback";
import PlanNotifications from "./PlanNotifications";
import { useToast } from "./Toast";
import { emitDataChanged } from "../services/events";

export default function RecruitmentAssistant({ jobs = [], jobsError = "", jobsLoading = false, floating = false, isOpen = true, onClose }) {
  const [message, setMessage] = useState("");
  const [jobId, setJobId] = useState("");
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmingPlan, setConfirmingPlan] = useState("");
  const toast = useToast();

  // Lets "these candidates" / "the next round" refer to the most recent proposal in this conversation.
  function lastPlanContext() {
    for (const item of [...messages].reverse()) {
      const plan = item.toolCalls?.find(tool => tool.result?.plan_id);
      if (plan) return { plan_id: plan.result.plan_id };
    }
    return null;
  }
  async function sendQuestion(question, selectedJobId = jobId, { appendUser = true } = {}) {
    const history = messages.map(item => ({
      role: item.role,
      content: item.role === "assistant" && item.toolCalls?.length
        ? `${item.content}\nRecorded HRMS tool results: ${JSON.stringify(item.toolCalls.map(summarizeToolCall))}`.slice(0, 2500)
        : item.content
    }));
    if (appendUser) {
      setMessages(current => [...current, { role: "user", content: question }]);
    }
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest("/recruitment/assistant", {
        method: "POST",
        body: { message: question, history, job_id: selectedJobId || null, ...(lastPlanContext() ? { context: lastPlanContext() } : {}) }
      });
      setMessages(current => [...current, { role: "assistant", content: result.message, toolCalls: result.tool_calls || [] }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function ask(event) {
    event.preventDefault();
    const question = message.trim();
    if (!question) return;
    setMessage("");
    await sendQuestion(question);
  }
  async function chooseMatchingJob(selectedJobId) {
    const lastUser = [...messages].reverse().find(item => item.role === "user");
    if (!lastUser || busy) return;
    setJobId(selectedJobId);
    await sendQuestion(lastUser.content, selectedJobId, { appendUser: true });
  }
  function mergePlan(planId, result) {
    setMessages(current => current.map(item => ({
      ...item,
      toolCalls: item.toolCalls?.map(tool => tool.result?.plan_id === planId
        ? {
          ...tool,
          result: { ...tool.result, ...result, plan_id: planId, counts: { ...tool.result.counts, ...result.counts } },
          status: "completed"
        }
        : tool)
    })));
  }
  async function confirmPlan(planId) {
    setConfirmingPlan(planId);
    setError("");
    try {
      const result = await apiRequest(`/interviews/scheduling-plans/${planId}/confirm`, { method: "POST" });
      mergePlan(planId, result);
      emitDataChanged("schedule");
      const booked = result.counts?.scheduled || 0;
      const failed = result.counts?.unable_to_schedule || 0;
      if (booked) toast.success(`${booked} interview${booked === 1 ? " is" : "s are"} booked and now in Your Schedule.${failed ? ` ${failed} could not be booked.` : ""}`);
      else toast.error("No interview could be booked. Review the reasons in the chat.");
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    } finally {
      setConfirmingPlan("");
    }
  }
  async function resendNotifications(planId) {
    setConfirmingPlan(planId);
    setError("");
    try {
      const result = await apiRequest(`/interviews/scheduling-plans/${planId}/resend-notifications`, { method: "POST" });
      mergePlan(planId, result);
      emitDataChanged("schedule");
      toast.success("Email retry finished. Messages that were already delivered were not sent again.");
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    } finally {
      setConfirmingPlan("");
    }
  }
  return (
    <section
      className={`panel assistant-panel${floating ? " assistant-floating-panel" : ""}${floating && !isOpen ? " assistant-floating-hidden" : ""}`}
      aria-label="Recruitment assistant chat"
      aria-hidden={floating && !isOpen}
    >
      <div className="panel-heading"><div><div className="eyebrow">AUTHORIZED HR TOOLS</div><h2>Recruitment assistant</h2><p className="muted">HR tasks only: jobs, applications, shortlisting, interviews, feedback and statuses. Booking and email sending always require your confirmation.</p></div>{floating ? <button className="assistant-close-button" type="button" aria-label="Close chat" onClick={onClose}>×</button> : <span className="metric-icon violet">✧</span>}</div>
      {messages.length > 0 && <div className="assistant-results" aria-live="polite">{messages.map((item, index) => <article className={`assistant-response assistant-${item.role}`} key={`${item.role}-${index}`}><b>{item.role === "user" ? "You" : "Assistant"}</b><p>{item.content}</p>{item.toolCalls?.map((tool, toolIndex) => <ToolResult key={`${tool.name}-${toolIndex}`} tool={tool} busy={confirmingPlan === tool.result?.plan_id || busy} onConfirm={confirmPlan} onResend={resendNotifications} onSelectJob={chooseMatchingJob} />)}</article>)}</div>}
      {jobsError && <ErrorMessage>Could not load jobs for the assistant: {jobsError}</ErrorMessage>}
      {!jobsError && jobsLoading && <p className="assistant-job-hint" role="status">Loading jobs…</p>}
      {!jobsError && !jobsLoading && jobs.length === 0 && <p className="assistant-job-hint">No jobs found. Create a job in Recruitment before asking about its applications.</p>}
      <form className={`assistant-form${jobs.length ? "" : " assistant-form-no-jobs"}`} onSubmit={ask}>{jobs.length > 0 && <select aria-label="Assistant job filter" value={jobId} onChange={e => setJobId(e.target.value)}><option value="">Select a job (optional)</option>{jobs.map(job => <option key={job.id} value={job.id}>{job.title} · {job.status}</option>)}</select>}<input required minLength="2" maxLength="1000" aria-label="Ask recruitment assistant" placeholder={'e.g. "Rank applicants by CGPA" or "Prepare a schedule plan for passed candidates"'} value={message} onChange={e => setMessage(e.target.value)} /><button className="button button-primary" disabled={busy}>{busy ? "Working…" : "Send"}</button></form>
      <ErrorMessage>{error}</ErrorMessage>
      {busy && <p className="muted" role="status">The assistant is checking authorized HRMS tools. No changes are made without your confirmation.</p>}
    </section>
  );
}

function summarizeToolCall(tool) {
  const result = tool.result || {};
  if (result.plan_id) {
    return {
      name: tool.name,
      status: tool.status,
      plan_id: result.plan_id,
      job_title: result.job_title,
      round: result.round?.name,
      counts: result.counts,
      items: result.items?.slice(0, 10).map(item => ({
        candidate_name: item.candidate_name,
        status: item.status,
        reason: item.reason
      }))
    };
  }
  if (result.results) {
    return {
      name: tool.name,
      status: tool.status,
      count: result.count,
      results: result.results.slice(0, 10).map(item => ({
        application_id: item.application_id,
        candidate_name: item.candidate_name,
        job_title: item.job_title,
        stage: item.stage,
        ats_score: item.ats_score
      }))
    };
  }
  return {
    name: tool.name,
    status: tool.status,
    result: result.draft_id ? {
      draft_id: result.draft_id,
      application_id: result.application_id,
      subject: result.subject,
      approval_status: result.approval_status
    } : result.error
  };
}

function JobChoices({ jobs, busy, onSelectJob }) {
  if (!jobs?.length || !onSelectJob) return null;
  return (
    <div className="assistant-job-choices">
      {jobs.map(job => (
        <button key={job.id} type="button" className="button button-quiet button-small" disabled={busy} onClick={() => onSelectJob(job.id)}>
          {job.title}
        </button>
      ))}
    </div>
  );
}

function ToolResult({ tool, busy, onConfirm, onResend, onSelectJob }) {
  const result = tool.result;
  if (!result) return <small>Tool {tool.name}: {tool.status}</small>;
  if (result.error) return <p role="status">Tool {tool.name} could not complete: {result.error}</p>;
  if (result.status === "clarification_required") {
    return (
      <div className="assistant-tool-result">
        <p role="status">{result.clarifying_question}</p>
        <JobChoices jobs={result.matching_jobs} busy={busy} onSelectJob={onSelectJob} />
      </div>
    );
  }
  if (result.plan_id) {
    return <div className="assistant-tool-result">
      <b>Schedule proposal · {result.job_title} · {result.round?.name}</b>
      <small>Window: {new Date(result.window_start).toLocaleString()} – {new Date(result.window_end).toLocaleString()} ({result.timezone})</small>
      <p>{result.counts?.eligible || 0} eligible · {result.counts?.awaiting_confirmation || 0} awaiting confirmation · {result.counts?.awaiting_evaluation || 0} awaiting evaluation · {result.counts?.unable_to_schedule || 0} unable to schedule</p>
      {result.round_inferred && <small>No round was named, so the earliest eligible round was used.</small>}
      {result.calendar_warning && <small role="alert">Calendar problem: {result.calendar_warning} Open Interviews → “Check Google Calendar connection” for setup steps.</small>}
      {(result.round?.required_interviewers?.length > 0 || result.round?.location) && <small>{result.round.required_interviewers?.length > 0 && `Interviewer: ${result.round.required_interviewers.join(", ")}`}{result.round.location ? ` · Meeting: ${result.round.location}` : ""}</small>}
      {result.items?.map(item => <small key={item.application_id}>{item.candidate_name}: {item.status.replaceAll("_", " ")}{item.starts_at ? ` — ${new Date(item.starts_at).toLocaleString(undefined, { timeZone: result.timezone })} (${result.timezone})` : ""}{item.pipeline_status ? ` · ${item.pipeline_status}` : ""}{item.email_status ? ` · email ${item.email_status.replaceAll("_", " ")}` : ""}{item.reason ? ` — ${item.reason}` : ""}</small>)}
      <PlanNotifications notifications={result.notifications} busy={busy} onResend={() => onResend(result.plan_id)} />
      {result.status === "awaiting_confirmation" && <button className="button button-primary button-small" disabled={busy || !(result.counts?.awaiting_confirmation)} onClick={() => onConfirm(result.plan_id)}>{busy ? "Rechecking availability…" : "Confirm plan and book"}</button>}
      <small>Candidate calendar availability is not checked. Interviewer availability is rechecked before booking; confirmed interviews are sent by email.</small>
    </div>;
  }
  if (result.results) {
    return <div className="assistant-tool-result">{result.results.length === 0 && <small>No matching applications were found.</small>}{result.results.map(item => {
      const id = item.application_id;
      return <div key={id || item.candidate_name}>
        <b>{item.candidate_name} · {item.job_title}</b>
        {item.ats_score != null && <small> ATS {item.ats_score}/100 · {item.matching_status}</small>}
        {item.semantic_evidence && <blockquote>{item.semantic_evidence}</blockquote>}
        {item.matching_evidence?.slice(0, 2).map((evidence, index) => <blockquote key={`${evidence.criterion}-${index}`}>{evidence.excerpt}</blockquote>)}
        {id && <Link className="text-link" to={`/recruitment/${id}`}>Review candidate →</Link>}
      </div>;
    })}</div>;
  }
  if (result.draft_id) {
    return <div className="assistant-tool-result"><b>Email draft saved for HR review</b><p>{result.recipient} · {result.subject}</p>{result.application_id && <Link className="text-link" to={`/recruitment/${result.application_id}`}>Review or edit draft →</Link>}</div>;
  }
  return <small>Tool {tool.name} completed.</small>;
}
