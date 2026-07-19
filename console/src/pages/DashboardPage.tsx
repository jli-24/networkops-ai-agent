import type { ConsoleClient } from "../api/client";
import { ErrorState, Loading } from "../components/AsyncState";
import { MetricCard } from "../components/MetricCard";
import { useConsoleQuery } from "../components/useConsoleQuery";

function percent(value: number | null): string {
  return value === null ? "N/A" : `${(value * 100).toFixed(1)}%`;
}

export function DashboardPage({ client }: { client: ConsoleClient }) {
  const query = useConsoleQuery(() => client.dashboard(), [client]);
  if (query.loading) return <Loading label="Loading dashboard…" />;
  if (query.error !== null) return <ErrorState denied={query.error === 403} onRetry={query.reload} />;
  if (!query.data) return null;

  const data = query.data;
  return (
    <section>
      <div className="page-heading"><div><p className="eyebrow">LIVE COMMAND OVERVIEW</p><h1>Operational posture</h1></div><span className="read-only-chip">READ ONLY</span></div>
      <div className="metrics-grid">
        <MetricCard label="Active Incident" value={data.active_incidents} note="open operational threads" tone="cyan" />
        <MetricCard label="High Risk Operation" value={data.high_risk_operations} note="high or critical assessments" tone="amber" />
        <MetricCard label="Policy Denied" value={data.policy_denied_count} note="blocked execution requests" tone="red" />
        <MetricCard label="Agent Success Rate" value={percent(data.agent_success_rate)} note="completed agent spans" tone="green" />
        <MetricCard label="RCA" value={percent(data.rca_accuracy)} note="latest evaluation accuracy" tone="cyan" />
        <MetricCard label="Workflow Latency" value={data.workflow_latency_ms === null ? "N/A" : `${data.workflow_latency_ms.toFixed(1)} ms`} note="aggregate workflow duration" tone="amber" />
      </div>
      <div className="dashboard-lower">
        <article className="surface posture-panel"><p className="eyebrow">CONTROL PLANE</p><h2>Console projection is online</h2><p>Current data is projected from the enterprise trace, governance, policy and evaluation stores. This console cannot mutate workflow state.</p><div className="signal-line"><span /><span /><span /><span /><span /></div></article>
        <article className="surface boundary-panel"><p className="eyebrow">TRUST BOUNDARY</p><dl><div><dt>Credential</dt><dd>Memory only</dd></div><div><dt>Transport</dt><dd>Bearer JWT</dd></div><div><dt>Operations</dt><dd>GET projection</dd></div></dl></article>
      </div>
    </section>
  );
}
