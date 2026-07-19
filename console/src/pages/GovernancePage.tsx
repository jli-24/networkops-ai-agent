import { useCallback, useState } from "react";

import type { ConsoleClient } from "../api/client";
import { EmptyState, ErrorState, Loading } from "../components/AsyncState";
import { CursorPagination } from "../components/CursorPagination";
import { StatusBadge } from "../components/StatusBadge";
import { useConsoleQuery } from "../components/useConsoleQuery";

export function GovernancePage({ client }: { client: ConsoleClient }) {
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const loader = useCallback(() => client.securityEvents(cursor), [client, cursor]);
  const query = useConsoleQuery(loader, [loader]);
  return <section><div className="page-heading"><div><p className="eyebrow">SECURITY EVENT CENTER</p><h1>Governance</h1></div></div>{query.loading ? <Loading label="Loading security events…" /> : query.error !== null ? <ErrorState denied={query.error === 403} onRetry={query.reload} /> : !query.data?.items.length ? <EmptyState>No security events found.</EmptyState> : <><div className="surface table-wrap"><table><thead><tr><th>Event Type</th><th>Severity</th><th>Actor</th><th>Incident</th><th>Decision</th><th>Time</th></tr></thead><tbody>{query.data.items.map((item, index) => <tr key={`${item.incident_id}-${item.timestamp}-${index}`}><td><strong>{item.event_type}</strong></td><td><StatusBadge value={item.severity} /></td><td>{item.actor_id ?? "system"}</td><td>{item.incident_id}</td><td>{item.decision ?? "—"}</td><td>{new Date(item.timestamp).toLocaleString()}</td></tr>)}</tbody></table></div><CursorPagination previous={history.length > 0} next={query.data.next_cursor} onPrevious={() => { const copy = [...history]; setCursor(copy.pop()); setHistory(copy); }} onNext={(next) => { setHistory((items) => [...items, cursor]); setCursor(next); }} /></>}</section>;
}
