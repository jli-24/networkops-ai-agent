import { useCallback, useState } from "react";

import type { ConsoleClient } from "../api/client";
import { EmptyState, ErrorState, Loading } from "../components/AsyncState";
import { StatusBadge } from "../components/StatusBadge";
import { useConsoleQuery } from "../components/useConsoleQuery";

export function EmbeddedLabPage({ client }: { client: ConsoleClient }) {
  const [goal, setGoal] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<number | null>(null);

  const tasksLoader = useCallback(() => client.embeddedTasks(), [client]);
  const tasks = useConsoleQuery(tasksLoader, [tasksLoader]);

  const detailLoader = useCallback(
    () => (selected ? client.embeddedTask(selected) : Promise.resolve(null)),
    [client, selected],
  );
  const detail = useConsoleQuery(detailLoader, [detailLoader]);

  const capabilitiesLoader = useCallback(() => client.capabilities(), [client]);
  const capabilities = useConsoleQuery(capabilitiesLoader, [capabilitiesLoader]);

  async function createTask() {
    if (!goal.trim() || submitting) return;
    setSubmitting(true);
    setActionError(null);
    try {
      const created = await client.embeddedCreateTask(goal.trim());
      setGoal("");
      setSelected(created.task_id);
      await tasks.reload();
    } catch (reason) {
      setActionError(reason instanceof Object && "status" in reason ? Number((reason as { status: number }).status) : 0);
    } finally {
      setSubmitting(false);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!selected) return;
    setActionError(null);
    try {
      await client.embeddedDecideApproval(selected, decision, "console-operator");
      await Promise.all([detail.reload(), tasks.reload()]);
    } catch (reason) {
      setActionError(reason instanceof Object && "status" in reason ? Number((reason as { status: number }).status) : 0);
    }
  }

  return (
    <section>
      <div className="page-heading">
        <div>
          <p className="eyebrow">VIRTUAL HARDWARE LAB</p>
          <h1>Embedded Lab</h1>
        </div>
      </div>

      <div className="surface embedded-composer">
        <label htmlFor="embedded-goal">Design goal</label>
        <input
          id="embedded-goal"
          value={goal}
          placeholder="e.g. 设计一个ESP32温湿度采集节点并验证"
          onChange={(event) => setGoal(event.target.value)}
        />
        <button type="button" onClick={() => void createTask()} disabled={submitting || !goal.trim()}>
          {submitting ? "Creating…" : "Create task"}
        </button>
        {actionError !== null && <p className="embedded-error">Request failed (HTTP {actionError}).</p>}
      </div>

      <div className="embedded-columns">
        <div className="embedded-tasks">
          <h2>Tasks</h2>
          {tasks.loading ? (
            <Loading label="Loading tasks…" />
          ) : tasks.error !== null ? (
            <ErrorState denied={tasks.error === 403} onRetry={tasks.reload} />
          ) : !tasks.data?.length ? (
            <EmptyState>No embedded tasks yet.</EmptyState>
          ) : (
            <ul className="embedded-task-list">
              {tasks.data.map((task) => (
                <li key={task.task_id}>
                  <button type="button" className={selected === task.task_id ? "active" : ""} onClick={() => setSelected(task.task_id)}>
                    <span className="task-id">{task.task_id}</span>
                    <StatusBadge value={task.status} />
                    <span className="task-state">{task.validation_state ?? "—"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="embedded-detail">
          <h2>Task detail</h2>
          {!selected ? (
            <EmptyState>Select a task to inspect its validation loop.</EmptyState>
          ) : detail.loading ? (
            <Loading label="Loading task…" />
          ) : detail.error !== null ? (
            <ErrorState denied={detail.error === 403} onRetry={detail.reload} />
          ) : detail.data ? (
            <article className="surface embedded-card">
              <header>
                <strong>{detail.data.task_id}</strong>
                <StatusBadge value={detail.data.status} />
                <span className="task-state">{detail.data.validation_state ?? "—"}</span>
              </header>
              <p className="task-goal">{detail.data.goal}</p>
              {detail.data.error && <p className="embedded-error">{detail.data.error}</p>}
              {detail.data.final_report && <p>{detail.data.final_report}</p>}

              {detail.data.approval_request && (
                <div className="embedded-approval">
                  <h3>Approval request</h3>
                  <p>
                    Action <strong>{detail.data.approval_request.action}</strong> on{" "}
                    <strong>{detail.data.approval_request.target}</strong> · risk{" "}
                    <strong>{detail.data.approval_request.risk_level}</strong>
                  </p>
                  <ol>
                    {detail.data.approval_request.execution_plan.map((step, index) => (
                      <li key={index}>{step}</li>
                    ))}
                  </ol>
                  {detail.data.status === "AWAITING_APPROVAL" && (
                    <div className="approval-actions">
                      <button type="button" className="approve" onClick={() => void decide("approve")}>Approve</button>
                      <button type="button" className="reject" onClick={() => void decide("reject")}>Reject</button>
                    </div>
                  )}
                </div>
              )}

              <h3>Artifacts</h3>
              {detail.data.artifacts.length === 0 ? (
                <EmptyState>No artifacts yet.</EmptyState>
              ) : (
                <table className="embedded-artifacts">
                  <thead>
                    <tr><th>Name</th><th>Type</th><th>SHA-256</th><th>By</th></tr>
                  </thead>
                  <tbody>
                    {detail.data.artifacts.map((artifact) => (
                      <tr key={artifact.artifact_id}>
                        <td>{artifact.name}</td>
                        <td>{artifact.type}</td>
                        <td className="hash">{artifact.sha256.slice(0, 12)}…</td>
                        <td>{artifact.created_by}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </article>
          ) : (
            <EmptyState>Task not found.</EmptyState>
          )}
        </div>
      </div>

      <div className="embedded-capabilities">
        <h2>Capabilities</h2>
        {capabilities.loading ? (
          <Loading label="Loading capabilities…" />
        ) : capabilities.error !== null ? (
          <ErrorState denied={capabilities.error === 403} onRetry={capabilities.reload} />
        ) : (
          <table className="embedded-artifacts">
            <thead>
              <tr><th>Name</th><th>Version</th><th>Type</th><th>Permission</th><th>Backend</th></tr>
            </thead>
            <tbody>
              {(capabilities.data ?? []).map((capability) => (
                <tr key={`${capability.name}-${capability.version}`}>
                  <td>{capability.name}</td>
                  <td>{capability.version}</td>
                  <td>{capability.type}</td>
                  <td>{capability.permission}</td>
                  <td>{capability.backend}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
