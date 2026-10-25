import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ErrorMessage } from "../components/Feedback";

export default function LoginPage() {
  const { authenticate } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await authenticate("/auth/login", form);
      navigate("/");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-page">
      <div className="auth-visual">
        <div className="brand brand-light"><span className="brand-mark">H</span><span>HRMS Automated Intelligence<small>HR WORKSPACE</small></span></div>
        <div className="auth-quote"><div className="quote-mark">“</div><h1>Great teams<br />start with great<br /><em>conversations.</em></h1><p>One calm, focused workspace to move every candidate forward.</p><div className="visual-card"><div className="visual-card-top"><span>THIS WEEK</span><b>Hiring overview</b></div><div className="visual-bars"><i /><i /><i /><i /><i /><i /><i /></div><div className="visual-caption"><span>New applicants</span><strong>+18.4%</strong></div></div></div>
        <div className="auth-foot">A thoughtful way to work with people.</div>
      </div>
      <main className="auth-panel"><div className="auth-form-wrap"><div className="eyebrow">WELCOME BACK</div><h2>Sign in to HRMS Automated Intelligence</h2><p className="muted">Your hiring workspace is ready when you are.</p><ErrorMessage>{error}</ErrorMessage>
        <form onSubmit={submit} className="stack-form"><label>Work email<input autoComplete="username" type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} placeholder="you@company.com" /></label><label>Password<input autoComplete="current-password" type="password" required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder="Enter your password" /></label><button className="button button-primary button-wide" disabled={busy}>{busy ? "Signing in…" : "Sign in"}<span>→</span></button></form>
        <div className="auth-switch">Have an invite? <Link to="/signup">Create your account</Link></div><div className="auth-secure">◈ <span>Secure HR access · Invite-only accounts</span></div></div></main>
    </div>
  );
}

