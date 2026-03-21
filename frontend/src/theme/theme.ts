export type Theme = "light" | "dark";

export function getInitialTheme(): Theme {
  const stored = localStorage.getItem("aitube-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("aitube-theme", theme);
}
