import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ErrorMessage } from "../components/Feedback";

export default function SignupPage() {
  const { authenticate } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setError("");
    if (form.password.length < 12) {
      setError("Use a password with at least 12 characters.");
      return;
    }
    setBusy(true);
    try {
      await authenticate("/auth/signup", form);
      navigate("/");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-page">
      <div className="auth-visual"><div className="brand brand-light"><span className="brand-mark">H</span><span>HRMS Automated Intelligence<small>HR WORKSPACE</small></span></div><div className="auth-quote"><div className="eyebrow eyebrow-light">A BETTER HIRING WORKFLOW</div><h1>Make room for<br /><em>good people.</em></h1><p>Keep every application, conversation and next step together.</p><div className="auth-feature-list"><span>✓ Candidate-first pipeline</span><span>✓ Private resume storage</span><span>✓ Interviews, all in one place</span></div></div><div className="auth-foot">A thoughtful way to work with people.</div></div>
      <main className="auth-panel"><div className="auth-form-wrap"><div className="eyebrow">TEAM ACCESS</div><h2>Create your account</h2><p className="muted">Create a recruiter account with your email and password.</p><ErrorMessage>{error}</ErrorMessage>
        <form onSubmit={submit} className="stack-form"><label>Work email<input type="email" required autoComplete="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} placeholder="you@company.com" /></label><label>Password<input type="password" required minLength="12" maxLength="128" autoComplete="new-password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder="At least 12 characters" /></label><button className="button button-primary button-wide" disabled={busy}>{busy ? "Creating account…" : "Create account"}<span>→</span></button></form>
        <div className="auth-switch">Already have an account? <Link to="/login">Sign in</Link></div><div className="auth-secure">◈ <span>New accounts have recruiter access; administrator access is restricted</span></div></div></main>
    </div>
  );
}
