import { useState, useEffect, useCallback } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";

import { Timeline } from "./components/Timeline";
import { Search } from "./components/Search";
import { TopicFlow } from "./components/TopicFlow";
import { EmbeddingsView } from "./components/EmbeddingsView";
import { Prediction } from "./components/Prediction";
import { SubscriptionManager } from "./components/SubscriptionManager";
import { QuarantinePage } from "./components/QuarantinePage";
import { WatchTimePage } from "./components/WatchTimePage";
import { EngagementPage } from "./components/EngagementPage";
import { CategorySettingsPage } from "./components/CategorySettingsPage";
import { NotFound } from "./components/NotFound";
import { ThemeToggle } from "./theme/ThemeToggle";
import { SettingsMenu } from "./components/SettingsMenu";
import { getInitialTheme, applyTheme } from "./theme/theme";
import type { Theme } from "./theme/theme";

function AppContent() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const handleToggle = useCallback((t: Theme) => setTheme(t), []);

  return (
    <>
      <header className="app-header">
        <img
          src="/aitube/images/logo.png"
          alt="AITube logo"
          className="app-logo"
          onClick={() => window.location.href = "/aitube/"}
        />
        <h1><span className="logo-ai">AI</span>Tube</h1>
        <nav>
          <NavLink to="/">Timeline</NavLink>
          <NavLink to="/search/">Search</NavLink>
          <NavLink to="/topic-flow/">Topic Flow</NavLink>
          <NavLink to="/embeddings/">Embeddings</NavLink>
          <NavLink to="/prediction/">Prediction</NavLink>
        </nav>
        <ThemeToggle theme={theme} onToggle={handleToggle} />
        <SettingsMenu />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/search/" element={<Search />} />
          <Route path="/topic-flow/" element={<TopicFlow />} />
          <Route path="/embeddings/" element={<EmbeddingsView />} />
          <Route path="/prediction/" element={<Prediction />} />
          <Route path="/subscriptions/" element={<SubscriptionManager />} />
          <Route path="/add-content/" element={<SubscriptionManager />} />
          <Route path="/quarantine/" element={<QuarantinePage />} />
          <Route path="/watch-time/" element={<WatchTimePage />} />
          <Route path="/engagement/" element={<EngagementPage />} />
          <Route path="/category-settings/" element={<CategorySettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/aitube/">
      <AppContent />
    </BrowserRouter>
  );
}
