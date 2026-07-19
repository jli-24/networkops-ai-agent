import { useCallback, useState } from "react";

import type { ConsoleClient } from "../api/client";
import { EmptyState, ErrorState, Loading } from "../components/AsyncState";
import { CursorPagination } from "../components/CursorPagination";
import { StatusBadge } from "../components/StatusBadge";
import { useConsoleQuery } from "../components/useConsoleQuery";

export function TracePage({ client, incidentId }: { client: ConsoleClient; incidentId: string }) {
  const [draft, setDraft] = useState(incidentId);
  const [selected, setSelected] = useState(incidentId);
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const loader = useCallback(() => selected ? client.trace(selected, cursor) : Promise.resolve({ items: [], next_cursor: null }), [client, selected, cursor]);
  const query = useConsoleQuery(loader, [loader]);
  return <section><div className="page-heading"><div><p className="eyebrow">EXECUTION TELEMETRY</p><h1>Agent trace</h1></div></div><form className="trace-search" onSubmit={(event) => { event.preventDefault(); setSelected(draft.trim()); setCursor(undefined); setHistory([]); }}><label>Incident ID<input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="INC-1001" /></label><button type="submit" disabled={!draft.trim()}>Load trace</button></form>{query.loading ? <Loading label="Loading trace…" /> : query.error !== null ? <ErrorState denied={query.error === 403} onRetry={query.reload} /> : !selected ? <EmptyState>Enter an incident ID to inspect its trace.</EmptyState> : !query.data?.items.length ? <EmptyState>No trace spans found.</EmptyState> : <><div className="trace-grid">{query.data.items.map((item) => <article className="surface trace-card" key={item.span_id}><div className="trace-card-top"><strong>{item.node ?? "Unnamed node"}</strong><StatusBadge value={item.status} /></div><dl><div><dt>Agent</dt><dd>{item.agent ?? "—"}</dd></div><div><dt>Tool</dt><dd>{item.tool ?? "—"}</dd></div><div><dt>Latency</dt><dd>{item.latency_ms === null ? "N/A" : `${item.latency_ms} ms`}</dd></div><div><dt>Timestamp</dt><dd>{new Date(item.timestamp).toLocaleString()}</dd></div><div><dt>Error Code</dt><dd>{item.error_code ?? "—"}</dd></div></dl></article>)}</div><CursorPagination previous={history.length > 0} next={query.data.next_cursor} onPrevious={() => { const copy = [...history]; setCursor(copy.pop()); setHistory(copy); }} onNext={(next) => { setHistory((items) => [...items, cursor]); setCursor(next); }} /></>}</section>;
}
