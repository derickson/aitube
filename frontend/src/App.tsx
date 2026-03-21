import { useState, useEffect, useCallback } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";

import { Timeline } from "./components/Timeline";
import { SubscriptionManager } from "./components/SubscriptionManager";
import { ThemeToggle } from "./theme/ThemeToggle";
import { getInitialTheme, applyTheme } from "./theme/theme";
import type { Theme } from "./theme/theme";

export default function App() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const handleToggle = useCallback((t: Theme) => setTheme(t), []);

  return (
    <BrowserRouter basename="/aitube/">
      <header className="app-header">
        <h1>AITube</h1>
        <nav>
          <NavLink to="/">Timeline</NavLink>
          <NavLink to="/subscriptions">Subscriptions</NavLink>
        </nav>
        <ThemeToggle theme={theme} onToggle={handleToggle} />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Timeline />} />
          <Route path="/subscriptions" element={<SubscriptionManager />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
