import { useLocation } from "react-router-dom";

export function NotFound() {
  const location = useLocation();

  return (
    <div className="not-found">
      <img
        src="/aitube/images/404.png"
        alt="404 Not Found"
        className="not-found-img"
      />
      <h2>Page Not Found</h2>
      <p className="not-found-path">
        <code>/aitube{location.pathname}</code> does not exist.
      </p>
      <a href="/aitube/" className="btn btn-primary">Back to Timeline</a>
    </div>
  );
}
