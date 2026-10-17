import { useState, useEffect, useCallback } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";

import { Timeline } from "./components/Timeline";
import { Search } from "./components/Search";
import { SearchExplain } from "./components/SearchExplain";
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
import { DemoModeProvider, useDemoMode } from "./demo/DemoMode";
import type { Theme } from "./theme/theme";

function AppContent() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const { demoMode, zenMode } = useDemoMode();

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
        {demoMode && (
          <span className="demo-badge" title="Demo Mode: showing AI and Software content only">
            Demo
          </span>
        )}
        {zenMode && (
          <span className="demo-badge" title="Zen Mode: AI and Software content hidden">
            Zen
          </span>
        )}
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
        {/* Keyed on Demo/Zen Mode so flipping them remounts the current page, which
            refetches everything under the new scope. */}
        <Routes key={demoMode ? "demo" : zenMode ? "zen" : "full"}>
          <Route path="/" element={<Timeline />} />
          <Route path="/search/" element={<Search />} />
          <Route path="/search-explain/" element={<SearchExplain />} />
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
      <DemoModeProvider>
        <AppContent />
      </DemoModeProvider>
    </BrowserRouter>
  );
}
