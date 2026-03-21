import { useState } from "react";

function copyToClipboard(text: string): boolean {
  // Fallback for non-secure contexts (http:// on non-localhost)
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  let success = false;
  try {
    success = document.execCommand("copy");
  } catch {
    success = false;
  }
  document.body.removeChild(textarea);
  return success;
}

export function ErrorBanner({ error }: { error: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    let success = false;
    try {
      await navigator.clipboard.writeText(error);
      success = true;
    } catch {
      success = copyToClipboard(error);
    }
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="error-banner">
      <span className="error-message">{error}</span>
      <button className="error-copy-btn" onClick={handleCopy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
