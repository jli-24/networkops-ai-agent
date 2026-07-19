import type {
  Dashboard,
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
  };
}

export type ConsoleClient = ReturnType<typeof createConsoleClient>;
