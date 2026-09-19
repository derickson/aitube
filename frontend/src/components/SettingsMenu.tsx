import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

function GearIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM6.5 8a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0Z" />
      <path d="M8.837 1.5c.294 0 .55.203.618.489l.213.9a5.6 5.6 0 0 1 1.14.472l.796-.483a.64.64 0 0 1 .782.097l.939.939a.64.64 0 0 1 .097.782l-.483.796c.213.354.379.735.472 1.14l.9.213a.636.636 0 0 1 .489.618v1.674a.636.636 0 0 1-.489.618l-.9.213a5.6 5.6 0 0 1-.472 1.14l.483.796a.64.64 0 0 1-.097.782l-.939.939a.64.64 0 0 1-.782.097l-.796-.483a5.6 5.6 0 0 1-1.14.472l-.213.9a.636.636 0 0 1-.618.489H7.163a.636.636 0 0 1-.618-.489l-.213-.9a5.6 5.6 0 0 1-1.14-.472l-.796.483a.64.64 0 0 1-.782-.097l-.939-.939a.64.64 0 0 1-.097-.782l.483-.796a5.6 5.6 0 0 1-.472-1.14l-.9-.213A.636.636 0 0 1 1.2 9.337V7.663c0-.294.203-.55.489-.618l.9-.213c.093-.405.259-.786.472-1.14l-.483-.796a.64.64 0 0 1 .097-.782l.939-.939a.64.64 0 0 1 .782-.097l.796.483c.354-.213.735-.379 1.14-.472l.213-.9a.636.636 0 0 1 .618-.489h1.674Zm-.51 1.273h-.654l-.19.803a.637.637 0 0 1-.477.474 4.36 4.36 0 0 0-1.42.588.637.637 0 0 1-.671-.016l-.71-.431-.462.462.431.71a.637.637 0 0 1 .016.671 4.36 4.36 0 0 0-.588 1.42.637.637 0 0 1-.474.477l-.803.19v.654l.803.19c.238.056.424.24.474.477.114.502.31 .973.588 1.42a.637.637 0 0 1-.016.671l-.431.71.462.462.71-.431a.637.637 0 0 1 .671-.016c.447.278.918.474 1.42.588.238.05.421.236.477.474l.19.803h.654l.19-.803a.637.637 0 0 1 .477-.474 4.36 4.36 0 0 0 1.42-.588.637.637 0 0 1 .671.016l.71.431.462-.462-.431-.71a.637.637 0 0 1-.016-.671c.278-.447.474-.918.588-1.42a.637.637 0 0 1 .474-.477l.803-.19v-.654l-.803-.19a.637.637 0 0 1-.474-.477 4.36 4.36 0 0 0-.588-1.42.637.637 0 0 1 .016-.671l.431-.71-.462-.462-.71.431a.637.637 0 0 1-.671.016 4.36 4.36 0 0 0-1.42-.588.637.637 0 0 1-.477-.474l-.19-.803Z" />
    </svg>
  );
}

export function SettingsMenu() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close]);

  return (
    <div className="settings-menu" ref={rootRef}>
      <button
        className="theme-toggle"
        aria-label="Settings"
        title="Settings"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <GearIcon />
      </button>
      {open && (
        <div className="settings-dropdown" role="menu">
          <NavLink to="/subscriptions/" role="menuitem" onClick={close}>
            Content
          </NavLink>
          <NavLink to="/quarantine/" role="menuitem" onClick={close}>
            Quarantine
          </NavLink>
          <NavLink to="/watch-time/" role="menuitem" onClick={close}>
            Watch Time
          </NavLink>
        </div>
      )}
    </div>
  );
}
