import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiRequest } from "../services/api";
import { ErrorMessage } from "../components/Feedback";

export default function PublicApplyPage() {
  const { publicToken } = useParams();
  const [job, setJob] = useState(null);
  const [answers, setAnswers] = useState({});
  const [form, setForm] = useState({ name: "", email: "", phone: "", education: "", experience_years: "" });
  const [resume, setResume] = useState(null);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiRequest(`/recruitment/public/jobs/${publicToken}`)
      .then(setJob)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [publicToken]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (!resume) {
      setError("Please attach your resume before submitting.");
      return;
    }
    const body = new FormData();
    body.append("name", form.name);
    body.append("email", form.email);
    body.append("phone", form.phone);
    body.append("education", form.education);
    if (form.experience_years !== "") body.append("experience_years", form.experience_years);
    body.append("answers", JSON.stringify(answers));
    body.append("resume", resume);
    setBusy(true);
    try {
      setReceipt(await apiRequest(`/recruitment/public/jobs/${publicToken}/applications`, { method: "POST", body }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="public-page"><div className="loading-row"><span className="spinner" />Loading role…</div></div>;
  if (receipt) return <div className="public-page"><div className="public-brand"><span className="brand-mark">H</span> HRMS Automated Intelligence</div><main className="public-card public-success"><div className="success-mark">✓</div><div className="eyebrow">APPLICATION RECEIVED</div><h1>Thank you for applying.</h1><p>{receipt.message}</p><p className="public-footnote">Your application is with the hiring team. We don’t share internal assessment scores through this form.</p></main></div>;
  if (!job) return <div className="public-page"><div className="public-brand"><span className="brand-mark">H</span> HRMS Automated Intelligence</div><main className="public-card"><ErrorMessage>{error || "This job is no longer accepting applications."}</ErrorMessage><Link className="text-link" to="/">Back to HRMS Automated Intelligence</Link></main></div>;

  const pastDeadline = job.deadline && new Date(job.deadline) <= new Date();
  const beforeOpening = job.opening_date && new Date(job.opening_date) > new Date();
  return (
    <div className="public-page">
      <div className="public-brand"><span className="brand-mark">H</span> HRMS Automated Intelligence <small>CAREERS</small></div>
      <div className="public-layout">
        <header className="public-job-header"><div className="eyebrow">CAREER OPPORTUNITY</div><h1>{job.title}</h1><div className="public-job-meta">{job.department && <span>{job.department}</span>}{job.location && <span>{job.location}</span>}{job.employment_type && <span>{job.employment_type}</span>}{job.minimum_experience != null && <span>{job.minimum_experience}+ years experience</span>}</div>
          {job.description && <p className="public-job-description">{job.description}</p>}
          {job.responsibilities?.length > 0 && <div className="public-requirements"><h3>What you’ll do</h3><ul>{job.responsibilities.map(item => <li key={item}>{item}</li>)}</ul></div>}
          {job.required_skills?.length > 0 && <div className="public-requirements"><h3>Required skills</h3><div className="skill-pills">{job.required_skills.map(skill => <span key={skill}>{skill}</span>)}</div></div>}
          {job.deadline && <div className="public-deadline">Applications close {new Intl.DateTimeFormat(undefined, { dateStyle: "long", timeZone: job.timezone }).format(new Date(job.deadline))} <span>{job.timezone}</span></div>}
        </header>
        <main className="public-card apply-card"><div className="eyebrow">YOUR APPLICATION</div><h2>Let’s get to know you.</h2><p className="muted">A few details to help us start the conversation.</p>
          {(pastDeadline || beforeOpening) ? <div className="public-closed"><b>{pastDeadline ? "Applications are closed" : "Applications aren’t open yet"}</b><p>{pastDeadline ? "The deadline for this role has passed." : `Applications open ${new Date(job.opening_date).toLocaleString(undefined, { timeZone: job.timezone })} (${job.timezone}).`}</p></div> :
            <form className="public-form" onSubmit={submit}>
              <ErrorMessage>{error}</ErrorMessage>
              <label>Full name<input required maxLength="180" autoComplete="name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
              <label>Email address<input required type="email" maxLength="320" autoComplete="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
              <label>Phone number <span className="optional-label">Optional</span><input type="tel" maxLength="40" autoComplete="tel" value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} /></label>
              {job.education_requirements?.length > 0 && <label>Education and qualifications <span className="field-help">Required for this role: {job.education_requirements.join(", ")}</span><textarea required rows="3" maxLength="5000" value={form.education} onChange={e => setForm({ ...form, education: e.target.value })} /></label>}
              <label>Relevant experience in years <span className="optional-label">Optional</span><input type="number" min="0" max="80" step="0.5" value={form.experience_years} onChange={e => setForm({ ...form, experience_years: e.target.value })} /></label>
              {job.questions?.map(question => <label key={question.key}>{question.label}{question.required && <span className="required-star"> *</span>}<textarea rows="3" maxLength="4000" required={question.required} value={answers[question.key] || ""} onChange={e => setAnswers(current => ({ ...current, [question.key]: e.target.value }))} /></label>)}
              <label>Resume <span className="field-help">PDF or DOCX · Up to 5 MB · Text-readable documents only</span><input required type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={e => setResume(e.target.files?.[0] || null)} /></label>
              {resume && <div className="selected-file"><span>▤</span>{resume.name}<small>{(resume.size / 1024 / 1024).toFixed(2)} MB</small></div>}
              <button className="button button-primary button-wide" disabled={busy}>{busy ? "Submitting…" : "Submit application"}<span>→</span></button>
              <p className="privacy-disclaimer">Your resume is shared securely with the hiring team for this position. This form does not display internal candidate scores.</p>
            </form>
          }
        </main>
      </div>
      <footer className="public-footer">A thoughtful way to work with people.</footer>
    </div>
  );
}
