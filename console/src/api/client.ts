import type {
  CapabilityInfo,
  Dashboard,
  EmbeddedTask,
  EmbeddedTaskDetail,
  EvaluationSummary,
  IncidentDetail,
  IncidentSummary,
  Page,
  PolicyDecision,
  SecurityEvent,
  TraceItem,
} from "./types";

export class ApiError extends Error {
  constructor(readonly status: number) {
    super(status === 403 ? "Permission denied" : "Console request failed");
  }
}

export interface IncidentFilters {
  status?: string;
  risk?: string;
  cursor?: string;
}

function query(values: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  params.set("limit", "50");
  return `?${params.toString()}`;
}

export function createConsoleClient(token: string, onUnauthorized: () => void) {
  async function get<T>(path: string): Promise<T> {
    const response = await fetch(path, {
      method: "GET",
      credentials: "omit",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.status === 401) {
      onUnauthorized();
      throw new ApiError(401);
    }
    if (!response.ok) throw new ApiError(response.status);
    return (await response.json()) as T;
  }

  async function post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(path, {
      method: "POST",
      credentials: "omit",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (response.status === 401) {
      onUnauthorized();
      throw new ApiError(401);
    }
    if (!response.ok) throw new ApiError(response.status);
    return (await response.json()) as T;
  }

  return {
    dashboard: () => get<Dashboard>("/api/v1/console/dashboard"),
    incidents: (filters: IncidentFilters = {}) =>
      get<Page<IncidentSummary>>(
        `/api/v1/console/incidents${query({
          status: filters.status,
          risk: filters.risk,
          cursor: filters.cursor,
        })}`,
      ),
    incident: (incidentId: string, cursor?: string) =>
      get<IncidentDetail>(
        `/api/v1/console/incidents/${encodeURIComponent(incidentId)}${query({ cursor })}`,
      ),
    trace: (incidentId: string, cursor?: string) =>
      get<Page<TraceItem>>(
        `/api/v1/console/incidents/${encodeURIComponent(incidentId)}/trace${query({ cursor })}`,
      ),
    securityEvents: (cursor?: string) =>
      get<Page<SecurityEvent>>(`/api/v1/console/security-events${query({ cursor })}`),
    policyDecisions: (cursor?: string) =>
      get<Page<PolicyDecision>>(`/api/v1/console/policy-decisions${query({ cursor })}`),
    evaluations: (cursor?: string) =>
      get<Page<EvaluationSummary>>(`/api/v1/console/evaluations${query({ cursor })}`),
    embeddedTasks: () => get<EmbeddedTask[]>("/api/v1/embedded/tasks"),
    embeddedTask: (taskId: string) =>
      get<EmbeddedTaskDetail>(`/api/v1/embedded/tasks/${encodeURIComponent(taskId)}`),
    embeddedCreateTask: (goal: string) =>
      post<EmbeddedTask>("/api/v1/embedded/tasks", { goal }),
    embeddedDecideApproval: (
      taskId: string,
      decision: "approve" | "reject",
      actor: string,
    ) =>
      post<EmbeddedTask>(
        `/api/v1/embedded/tasks/${encodeURIComponent(taskId)}/approval`,
        { decision, actor },
      ),
    capabilities: () => get<CapabilityInfo[]>("/api/v1/capabilities"),
  };
}

export type ConsoleClient = ReturnType<typeof createConsoleClient>;
