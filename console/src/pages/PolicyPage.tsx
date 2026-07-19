import { useCallback, useState } from "react";

import type { ConsoleClient } from "../api/client";
import { EmptyState, ErrorState, Loading } from "../components/AsyncState";
import { CursorPagination } from "../components/CursorPagination";
import { StatusBadge } from "../components/StatusBadge";
import { useConsoleQuery } from "../components/useConsoleQuery";

export function PolicyPage({ client }: { client: ConsoleClient }) {
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const loader = useCallback(() => client.policyDecisions(cursor), [client, cursor]);
  const query = useConsoleQuery(loader, [loader]);
  return <section><div className="page-heading"><div><p className="eyebrow">CONTEXTUAL CONTROL</p><h1>Policy decisions</h1></div></div>{query.loading ? <Loading label="Loading policy decisions…" /> : query.error !== null ? <ErrorState denied={query.error === 403} onRetry={query.reload} /> : !query.data?.items.length ? <EmptyState>No policy decisions found.</EmptyState> : <><div className="policy-grid">{query.data.items.map((item, index) => <article className="surface policy-card" key={`${item.policy_id}-${index}`}><div><span className="policy-id">{item.policy_id}</span><StatusBadge value={item.decision} /></div><h2>{item.operation}</h2><dl><div><dt>Incident</dt><dd>{item.incident_id}</dd></div><div><dt>Risk</dt><dd>{item.risk_level}</dd></div><div><dt>Time</dt><dd>{new Date(item.timestamp).toLocaleString()}</dd></div></dl></article>)}</div><CursorPagination previous={history.length > 0} next={query.data.next_cursor} onPrevious={() => { const copy = [...history]; setCursor(copy.pop()); setHistory(copy); }} onNext={(next) => { setHistory((items) => [...items, cursor]); setCursor(next); }} /></>}</section>;
}
