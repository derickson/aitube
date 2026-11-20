import { useState, useEffect, useCallback, type ReactElement } from "react";
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
import { DetoxPage } from "./components/DetoxPage";
import { NotFound } from "./components/NotFound";
import { ThemeToggle } from "./theme/ThemeToggle";
import { SettingsMenu } from "./components/SettingsMenu";
import { getInitialTheme, applyTheme } from "./theme/theme";
import { DemoModeProvider, useDemoMode } from "./demo/DemoMode";
import { DetoxProvider, useDetox } from "./detox/Detox";
import { FeaturesProvider, useFeatures } from "./features/Features";
import type { Theme } from "./theme/theme";

// Every page belongs to a feature (backend/app/features.py). A page whose feature is
// off for this deployment gets no route, so its URL falls through to NotFound; its
// nav / settings-menu link comes from the same feature list.
const PAGES: { feature: string; paths: string[]; element: ReactElement }[] = [
  { feature: "timeline", paths: ["/"], element: <Timeline /> },
  { feature: "search", paths: ["/search/"], element: <Search /> },
  { feature: "search_explain", paths: ["/search-explain/"], element: <SearchExplain /> },
  { feature: "topic_flow", paths: ["/topic-flow/"], element: <TopicFlow /> },
  { feature: "embeddings", paths: ["/embeddings/"], element: <EmbeddingsView /> },
  { feature: "prediction", paths: ["/prediction/"], element: <Prediction /> },
  { feature: "content", paths: ["/subscriptions/", "/add-content/"], element: <SubscriptionManager /> },
  { feature: "quarantine", paths: ["/quarantine/"], element: <QuarantinePage /> },
  { feature: "watch_time", paths: ["/watch-time/"], element: <WatchTimePage /> },
  { feature: "engagement", paths: ["/engagement/"], element: <EngagementPage /> },
  { feature: "category_settings", paths: ["/category-settings/"], element: <CategorySettingsPage /> },
  { feature: "detox", paths: ["/detox/"], element: <DetoxPage /> },
];

function AppContent() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const { demoMode, zenMode } = useDemoMode();
  const { active: detoxActive } = useDetox();
  const { features, has } = useFeatures();

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const handleToggle = useCallback((t: Theme) => setTheme(t), []);

  const navItems = features.filter((f) => f.kind === "nav" && f.route);

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
        {detoxActive && (
          <NavLink to="/detox/" className="demo-badge" title="Dopamine Detox: videos won't play">
            Detox
          </NavLink>
        )}
        <nav>
          {navItems.map((f) => (
            <NavLink key={f.id} to={f.route!} end={f.route === "/"}>
              {f.label}
            </NavLink>
          ))}
        </nav>
        <ThemeToggle theme={theme} onToggle={handleToggle} />
        <SettingsMenu />
      </header>
      <main>
        {/* Keyed on Demo/Zen Mode so flipping them remounts the current page, which
            refetches everything under the new scope. */}
        <Routes key={demoMode ? "demo" : zenMode ? "zen" : "full"}>
          {PAGES.filter((p) => has(p.feature)).flatMap((p) =>
            p.paths.map((path) => <Route key={path} path={path} element={p.element} />),
          )}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/aitube/">
      <FeaturesProvider>
        <DemoModeProvider>
          <DetoxProvider>
            <AppContent />
          </DetoxProvider>
        </DemoModeProvider>
      </FeaturesProvider>
    </BrowserRouter>
  );
}
