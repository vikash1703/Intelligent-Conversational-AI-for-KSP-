import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import AppShell from "./components/AppShell";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Chat from "./pages/Chat";
import Cases from "./pages/Cases";
import Network from "./pages/Network";
import HotspotMap from "./pages/HotspotMap";
import Analytics from "./pages/Analytics";
import Insights from "./pages/Insights";
import SocialInsights from "./pages/SocialInsights";
import Alerts from "./pages/Alerts";
import Profile from "./pages/Profile";

function ProtectedRoute({ children }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <AppShell>{children}</AppShell>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/home" element={<ProtectedRoute><Home /></ProtectedRoute>} />
      <Route path="/chat" element={<ProtectedRoute><Chat /></ProtectedRoute>} />
      <Route path="/cases" element={<ProtectedRoute><Cases /></ProtectedRoute>} />
      <Route path="/network" element={<ProtectedRoute><Network /></ProtectedRoute>} />
      <Route path="/map" element={<ProtectedRoute><HotspotMap /></ProtectedRoute>} />
      <Route path="/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
      <Route path="/insights" element={<ProtectedRoute><Insights /></ProtectedRoute>} />
      <Route path="/social-insights" element={<ProtectedRoute><SocialInsights /></ProtectedRoute>} />
      <Route path="/alerts" element={<ProtectedRoute><Alerts /></ProtectedRoute>} />
      <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
      <Route path="*" element={<Navigate to="/home" replace />} />
    </Routes>
  );
}
