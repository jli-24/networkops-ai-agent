import type { ReactNode } from "react";

export function MetricCard({ label, value, note, tone = "cyan" }: {
  label: string;
  value: ReactNode;
  note: string;
  tone?: "cyan" | "amber" | "red" | "green";
}) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <span className="metric-label">{label}</span>
      <strong className="metric-value">{value}</strong>
      <span className="metric-note">{note}</span>
    </article>
  );
}
