import { useCallback } from "react";
import type { Theme } from "./theme";

interface Props {
  theme: Theme;
  onToggle: (theme: Theme) => void;
}

function SunIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm0 1a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm.5-11.5a.5.5 0 0 0-1 0v1a.5.5 0 0 0 1 0v-1Zm0 14a.5.5 0 0 1-1 0v-1a.5.5 0 0 1 1 0v1ZM15.5 8a.5.5 0 0 1-.5.5h-1a.5.5 0 0 1 0-1h1a.5.5 0 0 1 .5.5ZM2 8.5a.5.5 0 0 0 0-1H1a.5.5 0 0 0 0 1h1Zm10.95-5.45a.5.5 0 0 1 0 .7l-.7.71a.5.5 0 1 1-.71-.71l.7-.7a.5.5 0 0 1 .71 0ZM3.76 12.95a.5.5 0 0 0 .7-.7l-.7-.71a.5.5 0 1 0-.71.71l.7.7Zm9.19 0a.5.5 0 0 1-.7 0l-.71-.7a.5.5 0 0 1 .71-.71l.7.7a.5.5 0 0 1 0 .71ZM3.76 3.05a.5.5 0 0 0-.7.7l.7.71a.5.5 0 1 0 .71-.71l-.71-.7Z"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M7.116 1.835a.5.5 0 0 1 .063.534 5.5 5.5 0 0 0 7.452 7.452.5.5 0 0 1 .67.65A7 7 0 1 1 6.466 1.772a.5.5 0 0 1 .65.063Z" />
    </svg>
  );
}

export function ThemeToggle({ theme, onToggle }: Props) {
  const toggle = useCallback(() => {
    onToggle(theme === "light" ? "dark" : "light");
  }, [theme, onToggle]);

  const nextLabel = theme === "light" ? "dark" : "light";

  return (
    <button
      onClick={toggle}
      className="theme-toggle"
      aria-label={`Switch to ${nextLabel} mode`}
      title={`Switch to ${nextLabel} mode`}
    >
      {theme === "light" ? <MoonIcon /> : <SunIcon />}
    </button>
  );
}
