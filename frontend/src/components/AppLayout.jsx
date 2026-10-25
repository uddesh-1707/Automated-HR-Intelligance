import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { apiRequest } from "../services/api";
import RecruitmentAssistant from "./RecruitmentAssistant";

export default function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [jobsError, setJobsError] = useState("");
  const [jobsLoading, setJobsLoading] = useState(true);

  useEffect(() => {
    let active = true;
    apiRequest("/recruitment/jobs")
      .then(result => {
        if (active) setJobs(result);
      })
      .catch(error => {
        if (active) setJobsError(error.message);
      })
      .finally(() => {
        if (active) setJobsLoading(false);
      });
    return () => { active = false; };
  }, []);

  function signOut() {
    logout();
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">H</span><span>HRMS Automated Intelligence<small>HR WORKSPACE</small></span></div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <NavLink to="/" end><span>◫</span> Overview</NavLink>
          <NavLink to="/recruitment"><span>♙</span> Recruitment</NavLink>
          <NavLink to="/interviews"><span>◷</span> Interviews</NavLink>

        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-note"><span>✦</span><div><b>Private by design</b><small>Candidate data is visible to your HR team only.</small></div></div>
          <div className="user-card"><div className="avatar">{user?.email?.[0]?.toUpperCase() || "H"}</div><div className="user-info"><b>{user?.email}</b><small>{user?.role === "admin" ? "Administrator" : "HR recruiter"}</small></div><button className="icon-button" title="Sign out" onClick={signOut}>↗</button></div>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar"><span className="breadcrumb">People operations <i>/</i> HR workspace</span><div className="topbar-user"><span className="status-dot" /> All systems normal</div></header>
        <div className="page-content"><Outlet /></div>
      </main>
      <div className="assistant-widget" role="complementary" aria-label="Recruitment assistant launcher">
        <RecruitmentAssistant jobs={jobs} jobsError={jobsError} jobsLoading={jobsLoading} floating isOpen={assistantOpen} onClose={() => setAssistantOpen(false)} />
        <button
          className={`assistant-launcher${assistantOpen ? " assistant-launcher-open" : ""}`}
          type="button"
          aria-label={assistantOpen ? "Close recruitment assistant" : "Open recruitment assistant"}
          aria-expanded={assistantOpen}
          onClick={() => setAssistantOpen(open => !open)}
        >
          {assistantOpen ? <span aria-hidden="true">×</span> : <span className="assistant-launcher-symbol" aria-hidden="true">✦</span>}
        </button>
      </div>
    </div>
  );
}
