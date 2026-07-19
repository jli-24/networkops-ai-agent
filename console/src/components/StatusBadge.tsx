export function StatusBadge({ value }: { value: string | null }) {
  const normalized = (value ?? "unknown").toLowerCase();
  const tone = ["succeeded", "allow", "approved", "low", "online"].includes(normalized)
    ? "good"
    : ["failed", "deny", "denied", "critical", "high"].includes(normalized)
      ? "bad"
      : "warn";
  return <span className={`status-badge status-${tone}`}>{value ?? "unknown"}</span>;
}
