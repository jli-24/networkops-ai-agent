import { useCallback, useState } from "react";

import type { ConsoleClient } from "../api/client";
import { EmptyState, ErrorState, Loading } from "../components/AsyncState";
import { CursorPagination } from "../components/CursorPagination";
import { useConsoleQuery } from "../components/useConsoleQuery";

const percent = (value: number | null) => value === null ? "N/A" : `${(value * 100).toFixed(1)}%`;
const unavailable = ["Retrieval", "Safety", "Latency"];

export function EvaluationPage({ client }: { client: ConsoleClient }) {
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const loader = useCallback(() => client.evaluations(cursor), [client, cursor]);
  const query = useConsoleQuery(loader, [loader]);
  return <section><div className="page-heading"><div><p className="eyebrow">OFFLINE QUALITY SIGNALS</p><h1>Evaluation</h1></div></div>{query.loading ? <Loading label="Loading evaluation results…" /> : query.error !== null ? <ErrorState denied={query.error === 403} onRetry={query.reload} /> : !query.data?.items.length ? <EmptyState>No evaluation results found.</EmptyState> : <><div className="evaluation-list">{query.data.items.map((item, index) => <article className="surface evaluation-card" key={`${item.dataset_version}-${index}`}><header><div><span className="evaluation-source">{item.source}</span><h2>{item.dataset_version}</h2></div><div className="case-count"><strong>{item.total_cases}</strong><span>Case count</span></div></header><div className="evaluation-metrics"><div><span>RCA</span><strong>{percent(item.rca_accuracy)}</strong></div><div><span>Top1</span><strong>{percent(item.top1_accuracy)}</strong></div><div><span>Top3</span><strong>{percent(item.top3_accuracy)}</strong></div></div><div className="unavailable-grid">{unavailable.map((label) => <div key={label}><span>{label}</span><strong>N/A</strong><small>not provided by current API</small></div>)}</div></article>)}</div><CursorPagination previous={history.length > 0} next={query.data.next_cursor} onPrevious={() => { const copy = [...history]; setCursor(copy.pop()); setHistory(copy); }} onNext={(next) => { setHistory((items) => [...items, cursor]); setCursor(next); }} /></>}</section>;
}
