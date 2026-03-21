import { useCallback } from "react";
import type { Theme } from "./theme";

interface Props {
  theme: Theme;
  onToggle: (theme: Theme) => void;
}

export function ThemeToggle({ theme, onToggle }: Props) {
  const toggle = useCallback(() => {
    onToggle(theme === "light" ? "dark" : "light");
  }, [theme, onToggle]);

  return (
    <button onClick={toggle} className="theme-toggle" aria-label="Toggle theme">
      {theme === "light" ? "Dark" : "Light"} Mode
    </button>
  );
}
