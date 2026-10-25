import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiRequest } from "../services/api";
import { EmptyState, ErrorMessage, StageBadge } from "../components/Feedback";
import { useToast } from "../components/Toast";
import { useDataRefresh } from "../services/events";

export default function CandidatesPage() {
  const [items, setItems] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [jobTitle, setJobTitle] = useState("");
  const [jobDetails, setJobDetails] = useState({
    department: "",
    description: "",
    responsibilities: "",
    required_skills: "",
    preferred_skills: "",
    minimum_experience: "",
    maximum_experience: "",
    deadline: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  });
  const [creating, setCreating] = useState(false);
  const [shortlist, setShortlist] = useState(null);
  const load = useCallback(() => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams();
    if (search.trim()) params.set("search", search.trim());
    if (stage) params.set("stage", stage);
    Promise.all([apiRequest(`/recruitment/applications?${params}`), apiRequest("/recruitment/jobs")])
      .then(([applications, activeJobs]) => {
        setItems(applications);
        setJobs(activeJobs);
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [search, stage]);
  useEffect(() => { load(); }, [load]);
  useDataRefresh(() => load(), { interval: 0 });
  async function createJob(event) {
    event.preventDefault();
    setCreating(true);
    setError("");
    try {
      await apiRequest("/recruitment/jobs", {
        method: "POST",
        body: {
          title: jobTitle,
          department: jobDetails.department || null,
          description: jobDetails.description || null,
          responsibilities: jobDetails.responsibilities.split("\n").map(item => item.trim()).filter(Boolean),
          required_skills: jobDetails.required_skills.split(",").map(item => item.trim()).filter(Boolean),
          preferred_skills: jobDetails.preferred_skills.split(",").map(item => item.trim()).filter(Boolean),
          minimum_experience: jobDetails.minimum_experience === "" ? null : Number(jobDetails.minimum_experience),
          maximum_experience: jobDetails.maximum_experience === "" ? null : Number(jobDetails.maximum_experience),
          deadline: jobDetails.deadline ? new Date(jobDetails.deadline).toISOString() : null,
          timezone: jobDetails.timezone,
          status: "draft"
        }
      });
      toast.success(`"${jobTitle}" saved as a draft. Publish it to get a public application link.`);
      setJobTitle("");
      setJobDetails(current => ({ ...current, department: "", description: "", responsibilities: "", required_skills: "", preferred_skills: "", minimum_experience: "", maximum_experience: "", deadline: "" }));
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }
  async function changeJobStatus(job, action) {
    setError("");
    try {
      const updated = await apiRequest(`/recruitment/jobs/${job.id}/${action}`, { method: "POST" });
      setJobs(current => current.map(item => item.id === updated.id ? updated : item));
      toast.success({ publish: `"${job.title}" is published and accepting applications.`, close: `Applications for "${job.title}" are closed.`, archive: `"${job.title}" was archived.` }[action] || "Job updated.");
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    }
  }
  async function deleteJob(job) {
    const count = job.application_count || 0;
    const warning = count
      ? `Delete "${job.title}" and its ${count} application${count === 1 ? "" : "s"}? Resumes, interviews and drafts for those candidates will be removed and any scheduled calendar events cancelled. This cannot be undone.`
      : `Delete "${job.title}"? Its public application form will stop working. This cannot be undone.`;
    if (!window.confirm(warning)) return;
    setError("");
    setNotice("");
    try {
      const result = await apiRequest(`/recruitment/jobs/${job.id}?delete_applications=${count > 0}`, { method: "DELETE" });
      setJobs(current => current.filter(item => item.id !== job.id));
      setNotice(`Deleted "${job.title}"${result?.deleted_applications ? ` and ${result.deleted_applications} application${result.deleted_applications === 1 ? "" : "s"}` : ""}.`);
      toast.success(`Deleted "${job.title}".`);
      load();
    } catch (err) {
      setError(err.message);
      toast.error(err.message);
    }
  }
  async function openShortlist(job) {
    setShortlist({ job, limit: job.shortlist_limit || 10, busy: false, result: null });
    try {
      const saved = await apiRequest(`/recruitment/jobs/${job.id}/shortlist`);
      setShortlist(current => current && current.job.id === job.id ? { ...current, limit: saved.limit || current.limit, result: saved.results.length ? saved : null } : current);
    } catch { /* no saved ranking yet */ }
  }
  async function runShortlist(apply) {
    const { job, limit } = shortlist;
    if (apply && !window.confirm(`Move the top ${limit} eligible candidates for "${job.title}" from Needs review to Screening? Nobody is rejected.`)) return;
    setShortlist(current => ({ ...current, busy: true }));
    setError("");
    try {
      const result = await apiRequest(`/recruitment/jobs/${job.id}/shortlist`, { method: "POST", body: { limit: Number(limit), apply } });
      setShortlist(current => ({ ...current, busy: false, result }));
      toast.success(`Shortlisting finished: ${result.counts.shortlisted || 0} shortlisted, ${result.counts.needs_review || 0} need review${apply ? `, ${result.moved_to_screening} moved to Screening` : ""}.`);
      load();
    } catch (err) {
      setShortlist(current => ({ ...current, busy: false }));
      setError(err.message);
      toast.error(err.message);
    }
  }
  async function copyLink(value) {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/apply/${value}`);
      toast.success("Application link copied to the clipboard.");
    } catch {
      setError("Could not copy the link automatically. Open the public URL directly.");
    }
  }
  return (
    <div>
      <div className="page-heading"><div><div className="eyebrow">TALENT & PEOPLE</div><h1>Recruitment<span className="heading-period">.</span></h1><p className="muted">A considered pipeline for every person who applies.</p></div><Link className="button button-primary" to="/google-forms">Application intake <span>↗</span></Link></div>
      <ErrorMessage>{error}</ErrorMessage>
      {notice && <div className="alert alert-success" role="status">{notice}</div>}
      <section className="panel job-create-panel">
        <div className="job-create-intro"><div className="metric-icon green">＋</div><div><h3>Create a job posting</h3><p>Draft a role, define requirements, then publish its public application link.</p></div></div>
        <form className="job-create-form job-create-expanded" onSubmit={createJob}>
          <div className="job-form-grid">
            <label>Job title<input required aria-label="Job title" minLength="2" maxLength="180" value={jobTitle} onChange={e => setJobTitle(e.target.value)} placeholder="e.g. Backend Developer" /></label>
            <label>Department or team<input value={jobDetails.department} onChange={e => setJobDetails({ ...jobDetails, department: e.target.value })} placeholder="Engineering" /></label>
            <label className="job-form-wide">Description<textarea rows="3" maxLength="10000" value={jobDetails.description} onChange={e => setJobDetails({ ...jobDetails, description: e.target.value })} placeholder="Describe the role and what success looks like" /></label>
            <label>Required skills <small>Comma-separated</small><input value={jobDetails.required_skills} onChange={e => setJobDetails({ ...jobDetails, required_skills: e.target.value })} placeholder="Python, distributed systems" /></label>
            <label>Preferred skills <small>Comma-separated</small><input value={jobDetails.preferred_skills} onChange={e => setJobDetails({ ...jobDetails, preferred_skills: e.target.value })} placeholder="AWS, Kubernetes" /></label>
            <label className="job-form-wide">Responsibilities <small>One per line</small><textarea rows="3" value={jobDetails.responsibilities} onChange={e => setJobDetails({ ...jobDetails, responsibilities: e.target.value })} placeholder={"Build reliable services\nCollaborate across teams"} /></label>
            <label>Minimum experience<input type="number" min="0" max="80" step="0.5" value={jobDetails.minimum_experience} onChange={e => setJobDetails({ ...jobDetails, minimum_experience: e.target.value })} /></label>
            <label>Maximum experience<input type="number" min="0" max="80" step="0.5" value={jobDetails.maximum_experience} onChange={e => setJobDetails({ ...jobDetails, maximum_experience: e.target.value })} /></label>
            <label>Application deadline<input type="datetime-local" value={jobDetails.deadline} onChange={e => setJobDetails({ ...jobDetails, deadline: e.target.value })} /></label>
            <label>Timezone<input value={jobDetails.timezone} onChange={e => setJobDetails({ ...jobDetails, timezone: e.target.value })} /></label>
          </div>
          <button className="button button-dark" disabled={creating}>{creating ? "Saving…" : "Save as draft"}</button>
        </form>
      </section>
      {jobs.length > 0 && <section className="jobs-overview"><div className="panel-heading"><div><div className="eyebrow">OPENINGS</div><h2>Job postings <span className="count-pill">{jobs.length}</span></h2></div></div><div className="job-cards">{jobs.map(job => <article className="panel job-card" key={job.id}><div className="job-card-heading"><div><h3>{job.title}</h3><p>{job.department || "Team not specified"} · {job.application_count} application{job.application_count === 1 ? "" : "s"}</p></div><span className={`badge job-status-${job.status}`}>{job.status}</span></div>{job.deadline && <div className="job-deadline">Deadline · {new Date(job.deadline).toLocaleString(undefined, { timeZone: job.timezone })}</div>}{job.public_url && <div className="job-public-link"><a href={`${window.location.origin}/apply/${job.public_url.split("/").pop()}`} target="_blank" rel="noreferrer">Open public form ↗</a><button className="button button-quiet button-small" onClick={() => copyLink(job.public_url.split("/").pop())}>Copy link</button></div>}<div className="job-card-actions">{job.status === "draft" && <button className="button button-primary button-small" onClick={() => changeJobStatus(job, "publish")}>Publish role</button>}{job.status === "published" && <button className="button button-quiet button-small" onClick={() => changeJobStatus(job, "close")}>Close applications</button>}{job.status === "closed" && <button className="button button-quiet button-small" onClick={() => changeJobStatus(job, "archive")}>Archive</button>}<button className="button button-quiet button-small" onClick={() => { setStage(""); setSearch(job.title); }}>View applicants</button><button className="button button-quiet button-small" onClick={() => openShortlist(job)}>Shortlist resumes</button><button className="button button-quiet button-small danger-text" onClick={() => deleteJob(job)}>Delete form</button></div></article>)}</div></section>}
      {shortlist && <section className="panel shortlist-panel" aria-label="Resume shortlisting">
        <div className="panel-heading"><div><div className="eyebrow">EXPLAINABLE RESUME SHORTLISTING</div><h2>{shortlist.job.title}</h2><p className="muted">Resumes are compared with the job description, required and preferred skills and responsibilities, using synonyms, semantic similarity and an optional LLM check. Missing evidence is never treated as proof of a missing skill.</p></div><button className="button button-quiet button-small" onClick={() => setShortlist(null)}>Close</button></div>
        <div className="shortlist-controls"><label>Shortlist size<input type="number" min="1" max="500" value={shortlist.limit} onChange={event => setShortlist({ ...shortlist, limit: event.target.value })} /></label><button className="button button-primary" disabled={shortlist.busy} onClick={() => runShortlist(false)}>{shortlist.busy ? "Assessing resumes…" : "Run shortlisting"}</button><button className="button button-quiet" disabled={shortlist.busy} onClick={() => runShortlist(true)}>Run and move shortlisted to Screening</button></div>
        {shortlist.result?.results?.length ? shortlist.result.results.map(row => <article className="shortlist-row" key={row.application_id}><div className="shortlist-rank">{row.rank ? `#${row.rank}` : "—"}</div><div><Link className="text-link" to={`/recruitment/${row.application_id}`}><b>{row.candidate_name}</b></Link><p>{row.explanation}</p><div>{row.required_skills?.map(skill => <span key={skill.requirement} className={`evidence-chip evidence-${skill.status}`} title={skill.evidence || "No evidence found in the resume"}>{skill.requirement}: {skill.status.replaceAll("_", " ")}</span>)}</div></div><div><span className={`badge status-${row.status}`}>{row.status.replaceAll("_", " ")}</span><p>{row.score == null ? "" : `${row.score}/100`}</p></div></article>) : <p className="muted">{shortlist.busy ? "" : "No shortlisting has been run for this job yet."}</p>}
      </section>}
      <section className="panel list-panel"><div className="list-toolbar"><div><h2>Applications <span className="count-pill">{items.length}</span></h2><p className="muted">Review people across your hiring pipeline.</p></div><div className="filters"><form className="search-box" onSubmit={e => e.preventDefault()}><span>⌕</span><input aria-label="Search applications" placeholder="Search name, email or role" value={search} onChange={e => setSearch(e.target.value)} /></form><select aria-label="Filter stage" value={stage} onChange={e => setStage(e.target.value)}><option value="">All stages</option><option value="needs_review">Needs review</option><option value="screening">Screening</option><option value="interview">Interview</option><option value="offer">Offer</option><option value="hired">Hired</option><option value="rejected">Rejected</option></select></div></div>
        {loading ? <div className="loading-row"><span className="spinner" />Loading candidates…</div> : items.length === 0 ? <EmptyState title="No applications yet">Applications submitted through your form will appear here. Create an open role to get started.</EmptyState> : <div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Role</th><th>Experience</th><th>Applied</th><th>ATS assistance</th><th>Stage</th><th></th></tr></thead><tbody>{items.map(item => <tr key={item.id}><td><Link className="candidate-cell" to={`/recruitment/${item.id}`}><span className="avatar avatar-table">{item.candidate.name[0]?.toUpperCase()}</span><span><b>{item.candidate.name}</b><small>{item.candidate.email}</small></span></Link></td><td>{item.job.title}</td><td>{item.candidate.experience_years == null ? "—" : `${item.candidate.experience_years} yrs`}</td><td>{new Date(item.applied_at).toLocaleDateString()}</td><td>{item.ats_score == null ? <span className="badge status-pending">{item.matching_status}</span> : <b className="ats-table-score">{item.ats_score}/100</b>}</td><td><StageBadge stage={item.stage} />{item.pipeline_status && <small className="pipeline-chip pipeline-chip-table">{item.pipeline_status}</small>}</td><td><Link className="row-link" aria-label={`Review ${item.candidate.name}`} to={`/recruitment/${item.id}`}>→</Link></td></tr>)}</tbody></table></div>}
      </section>

      {jobs.length > 0 && <p className="subtle-foot">{jobs.length} active role{jobs.length === 1 ? "" : "s"} · job titles are matched exactly by the intake form</p>}
    </div>
  );
}
