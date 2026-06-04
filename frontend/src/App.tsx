import { useState, useEffect, useCallback } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";

import { Timeline } from "./components/Timeline";
import { Search } from "./components/Search";
import { TopicFlow } from "./components/TopicFlow";
import { Prediction } from "./components/Prediction";
import { SubscriptionManager } from "./components/SubscriptionManager";
import { NotFound } from "./components/NotFound";
import { ThemeToggle } from "./theme/ThemeToggle";
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
          <NavLink to="/prediction/">Prediction</NavLink>
          <NavLink to="/subscriptions/">Subscriptions</NavLink>
        </nav>
        <ThemeToggle theme={theme} onToggle={handleToggle} />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/search/" element={<Search />} />
          <Route path="/topic-flow/" element={<TopicFlow />} />
          <Route path="/prediction/" element={<Prediction />} />
          <Route path="/subscriptions/" element={<SubscriptionManager />} />
          <Route path="/add-content/" element={<SubscriptionManager />} />
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
