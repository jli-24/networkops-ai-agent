import type { ReactNode } from "react";

export function Loading({ label }: { label: string }) {
  return <div className="state-panel loading-state"><span className="spinner" />{label}</div>;
}

export function ErrorState({ denied, onRetry }: { denied: boolean; onRetry: () => void }) {
  return (
    <div className="state-panel error-state" role="alert">
      <strong>{denied ? "Permission denied" : "Unable to load console data"}</strong>
      <span>{denied ? "Your identity does not have access to this projection." : "The service did not return a usable response."}</span>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="state-panel empty-state">{children}</div>;
}
