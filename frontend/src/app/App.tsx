import { Navigate, Route, Routes } from "react-router-dom";
import { ApiProvider } from "../api/http";
import Shell from "../components/Shell";
import LivePage from "../pages/LivePage";
import LeaderboardPage from "../pages/LeaderboardPage";
import LoginPage from "../pages/LoginPage";
import ModelsPage from "../pages/ModelsPage";

export default function App() {
  return (
    <ApiProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <Shell>
              <LivePage />
            </Shell>
          }
        />
        <Route
          path="/leaderboard"
          element={
            <Shell>
              <LeaderboardPage />
            </Shell>
          }
        />
        <Route
          path="/models"
          element={
            <Shell>
              <ModelsPage />
            </Shell>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ApiProvider>
  );
}

