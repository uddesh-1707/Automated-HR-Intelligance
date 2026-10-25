import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import AppLayout from "./components/AppLayout";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";
import DashboardPage from "./pages/DashboardPage";
import CandidatesPage from "./pages/CandidatesPage";
import CandidateDetailPage from "./pages/CandidateDetailPage";
import InterviewsPage from "./pages/InterviewsPage";
import GoogleSetupPage from "./pages/GoogleSetupPage";
import PublicApplyPage from "./pages/PublicApplyPage";
import EvaluationPage from "./pages/EvaluationPage";

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="screen-center"><span className="spinner" />Loading your workspace…</div>;
  return user ? children : <Navigate to="/login" replace />;
}

export default function App() {
  const { user } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/signup" element={user ? <Navigate to="/" replace /> : <SignupPage />} />
      <Route path="/apply/:publicToken" element={<PublicApplyPage />} />
      <Route path="/evaluate/:token" element={<EvaluationPage />} />
      <Route path="/" element={<Protected><AppLayout /></Protected>}>
        <Route index element={<DashboardPage />} />
        <Route path="recruitment" element={<CandidatesPage />} />
        <Route path="recruitment/:applicationId" element={<CandidateDetailPage />} />
        <Route path="interviews" element={<InterviewsPage />} />
        <Route path="google-forms" element={<GoogleSetupPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
