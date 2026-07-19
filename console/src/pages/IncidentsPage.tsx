import { useCallback, useRef, useState } from "react";

import type { ConsoleClient } from "../api/client";
import type { IncidentDetail } from "../api/types";
import { ApiError } from "../api/client";
import { EmptyState, ErrorState, Loading } from "../components/AsyncState";
import { CursorPagination } from "../components/CursorPagination";
import { StatusBadge } from "../components/StatusBadge";
import { useConsoleQuery } from "../components/useConsoleQuery";

export function IncidentsPage({ client, onOpenTrace }: { client: ConsoleClient; onOpenTrace: (id: string) => void }) {
  const [status, setStatus] = useState("");
  const [risk, setRisk] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [detailRequest, setDetailRequest] = useState<{ id: string; cursor?: string } | null>(null);
  const [detailHistory, setDetailHistory] = useState<Array<string | undefined>>([]);
  const [detailError, setDetailError] = useState<number | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailGeneration = useRef(0);
  const loader = useCallback(() => client.incidents({ status, risk, cursor }), [client, status, risk, cursor]);
  const query = useConsoleQuery(loader, [loader]);

  function reset(nextStatus: string, nextRisk: string) {
    setStatus(nextStatus); setRisk(nextRisk); setCursor(undefined); setHistory([]);
  }

  async function showDetail(incidentId: string, detailCursor?: string) {
    const requestGeneration = ++detailGeneration.current;
    setDetailRequest({ id: incidentId, cursor: detailCursor });
    setDetail(null); setDetailLoading(true); setDetailError(null);
    try {
      const response = await client.incident(incidentId, detailCursor);
      if (detailGeneration.current === requestGeneration) setDetail(response);
    } catch (reason) {
      if (detailGeneration.current === requestGeneration) setDetailError(reason instanceof ApiError ? reason.status : 0);
    } finally {
      if (detailGeneration.current === requestGeneration) setDetailLoading(false);
    }
  }

  return (
    <section>
      <div className="page-heading"><div><p className="eyebrow">INCIDENT COMMAND</p><h1>Incident operations</h1></div></div>
      <div className="filter-bar">
        <label>Status filter<select value={status} onChange={(e) => reset(e.target.value, risk)}><option value="">All statuses</option><option value="running">Running</option><option value="interrupted">Interrupted</option><option value="succeeded">Succeeded</option><option value="failed">Failed</option></select></label>
        <label>Risk filter<select value={risk} onChange={(e) => reset(status, e.target.value)}><option value="">All risks</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select></label>
      </div>
      {query.loading ? <Loading label="Loading incidents…" /> : query.error !== null ? <ErrorState denied={query.error === 403} onRetry={query.reload} /> : !query.data?.items.length ? <EmptyState>No incidents found.</EmptyState> : (
        <div className="surface table-wrap"><table><thead><tr><th>Incident</th><th>Status</th><th>Risk</th><th>Runs</th><th>Spans</th><th>Updated</th><th /></tr></thead><tbody>{query.data.items.map((item) => <tr key={item.incident_id}><td><strong>{item.incident_id}</strong></td><td><StatusBadge value={item.status} /></td><td><StatusBadge value={item.risk} /></td><td>{item.run_count}</td><td>{item.span_count}</td><td>{formatTime(item.updated_at)}</td><td><button className="link-button" type="button" aria-label={`View ${item.incident_id} details`} onClick={() => { setDetailHistory([]); void showDetail(item.incident_id); }}>Inspect</button></td></tr>)}</tbody></table></div>
      )}
      <CursorPagination previous={history.length > 0} next={query.data?.next_cursor} onPrevious={() => { const copy = [...history]; setCursor(copy.pop()); setHistory(copy); }} onNext={(next) => { setHistory((items) => [...items, cursor]); setCursor(next); }} />
      {detailLoading && <Loading label="Loading incident detail…" />}
      {detailError !== null && <ErrorState denied={detailError === 403} onRetry={() => detailRequest && void showDetail(detailRequest.id, detailRequest.cursor)} />}
      {detail && !detailLoading && <IncidentPanel detail={detail} onOpenTrace={onOpenTrace} previous={detailHistory.length > 0} onPrevious={() => { if (!detailRequest) return; const copy = [...detailHistory]; const previous = copy.pop(); setDetailHistory(copy); void showDetail(detailRequest.id, previous); }} onNext={(next) => { if (!detailRequest) return; setDetailHistory((items) => [...items, detailRequest.cursor]); void showDetail(detailRequest.id, next); }} />}
    </section>
  );
}

function IncidentPanel({ detail, onOpenTrace, previous, onPrevious, onNext }: { detail: IncidentDetail; onOpenTrace: (id: string) => void; previous: boolean; onPrevious: () => void; onNext: (cursor: string) => void }) {
  return <article className="surface detail-panel"><div className="detail-header"><div><p className="eyebrow">INCIDENT DETAIL</p><h2>{detail.incident_id}</h2></div><button type="button" aria-label={`Open ${detail.incident_id} trace`} onClick={() => onOpenTrace(detail.incident_id)}>Open trace</button></div><div className="detail-grid"><div><span>Status</span><strong>{detail.status}</strong></div><div><span>Risk</span><strong>{detail.risk ?? "unknown"}</strong></div><div><span>Stage</span><strong>{detail.current_stage ?? "N/A"}</strong></div><div><span>Approval</span><strong>{detail.approval_status ?? "N/A"}</strong></div><div><span>Execution</span><strong className="detail-value">{detail.execution_status ?? "N/A"}</strong></div></div><h3>Timeline</h3>{detail.timeline.length ? <ol className="timeline">{detail.timeline.map((item, index) => <li key={`${item.timestamp}-${index}`}><span className="timeline-node" /><div><strong>{item.name}</strong><span>{item.event_type} · {item.source}</span></div><StatusBadge value={item.status} /><time>{formatTime(item.timestamp)}</time></li>)}</ol> : <EmptyState>No timeline events found.</EmptyState>}<CursorPagination previous={previous} next={detail.timeline_next_cursor} onPrevious={onPrevious} onNext={onNext} /></article>;
}

function formatTime(value: string | null): string { return value ? new Date(value).toLocaleString() : "N/A"; }
