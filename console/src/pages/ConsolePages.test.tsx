import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const dashboard = {
  active_incidents: 4,
  high_risk_operations: 2,
  policy_denied_count: 1,
  agent_success_rate: 0.95,
  rca_accuracy: 0.8,
  workflow_latency_ms: 125,
};

async function authenticate(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("JWT access token"), "jwt-value");
  await user.click(screen.getByRole("button", { name: "Connect console" }));
  await screen.findByText("95.0%");
}

describe("Operator Console pages", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders incidents, filters, cursor pagination, detail and trace", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.includes("/incidents/INC-1/trace")) {
        return jsonResponse({
          items: [
            {
              span_id: "span-1",
              parent_id: null,
              node: "RiskCheck",
              agent: null,
              tool: null,
              status: "succeeded",
              latency_ms: 18.2,
              timestamp: "2026-07-19T08:00:00Z",
              error_code: null,
            },
          ],
          next_cursor: null,
        });
      }
      if (url.includes("/incidents/INC-1")) {
        return jsonResponse({
          incident_id: "INC-1",
          status: "executed",
          risk: "high",
          current_stage: "report",
          approval_status: "approve",
          execution_status: "succeeded",
          timeline: [
            {
              source: "trace",
              event_type: "agent",
              name: "DiagnosisAgent",
              status: "succeeded",
              timestamp: "2026-07-19T08:00:00Z",
              latency_ms: 20,
              error_code: null,
            },
          ],
          timeline_next_cursor: null,
        });
      }
      if (url.includes("/incidents")) {
        return jsonResponse({
          items: [
            {
              incident_id: "INC-1",
              status: "succeeded",
              risk: "high",
              started_at: "2026-07-19T08:00:00Z",
              updated_at: "2026-07-19T08:01:00Z",
              run_count: 1,
              span_count: 6,
            },
          ],
          next_cursor: url.includes("cursor=next-1") ? null : "next-1",
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await authenticate(user);

    await user.click(screen.getByRole("button", { name: "Incidents" }));
    expect(await screen.findByText("INC-1")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Status filter"), "succeeded");
    await user.selectOptions(screen.getByLabelText("Risk filter"), "high");
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) =>
        String(url).includes("status=succeeded&risk=high"),
      )).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) =>
        String(url).includes("cursor=next-1"),
      )).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: "View INC-1 details" }));
    expect(await screen.findByText("DiagnosisAgent")).toBeInTheDocument();
    expect(screen.getByText("approve")).toBeInTheDocument();
    expect(screen.getByText("succeeded", { selector: ".detail-value" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open INC-1 trace" }));
    expect(await screen.findByText("RiskCheck")).toBeInTheDocument();
    expect(screen.getByText("18.2 ms")).toBeInTheDocument();
  });

  it("renders governance, policy and evaluation projections", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.includes("security-events")) {
        return jsonResponse({
          items: [{
            incident_id: "INC-7",
            actor_id: "admin-1",
            event_type: "policy_violation",
            severity: "high",
            decision: "denied",
            timestamp: "2026-07-19T08:00:00Z",
          }],
          next_cursor: null,
        });
      }
      if (url.includes("policy-decisions")) {
        return jsonResponse({
          items: ["ALLOW", "DENY", "REQUIRE_APPROVAL"].map((decision) => ({
            incident_id: `INC-${decision}`,
            policy_id: `policy-${decision}`,
            decision,
            operation: "restart_device",
            risk_level: "high",
            timestamp: "2026-07-19T08:00:00Z",
          })),
          next_cursor: null,
        });
      }
      if (url.includes("evaluations")) {
        return jsonResponse({
          items: [{
            source: "evaluation",
            dataset_version: "fault-v1",
            total_cases: 10,
            passed_cases: 8,
            rca_accuracy: 0.8,
            top1_accuracy: 0.7,
            top3_accuracy: 0.9,
            created_at: "2026-07-19T08:00:00Z",
          }],
          next_cursor: null,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await authenticate(user);

    await user.click(screen.getByRole("button", { name: "Governance" }));
    expect(await screen.findByText("policy_violation")).toBeInTheDocument();
    expect(screen.getByText("admin-1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Policy" }));
    for (const decision of ["ALLOW", "DENY", "REQUIRE_APPROVAL"]) {
      expect(await screen.findByText(decision)).toBeInTheDocument();
    }

    await user.click(screen.getByRole("button", { name: "Evaluation" }));
    expect(await screen.findByText("80.0%")).toBeInTheDocument();
    expect(screen.getByText("70.0%")).toBeInTheDocument();
    expect(screen.getByText("90.0%")).toBeInTheDocument();
    const unavailable = screen.getAllByText("N/A");
    expect(unavailable).toHaveLength(3);
    expect(
      screen.getAllByText("not provided by current API"),
    ).toHaveLength(3);
  });

  it("shows empty states", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      return jsonResponse({ items: [], next_cursor: null });
    });
    const user = userEvent.setup();
    render(<App />);
    await authenticate(user);

    await user.click(screen.getByRole("button", { name: "Governance" }));
    expect(await screen.findByText("No security events found.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Policy" }));
    expect(await screen.findByText("No policy decisions found.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Evaluation" }));
    expect(await screen.findByText("No evaluation results found.")).toBeInTheDocument();
  });

  it("retries the requested incident detail after an initial failure", async () => {
    let detailAttempts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.includes("/incidents/INC-1")) {
        detailAttempts += 1;
        if (detailAttempts === 1) return jsonResponse({ detail: "Unavailable" }, 503);
        return jsonResponse({
          incident_id: "INC-1", status: "succeeded", risk: "high",
          current_stage: "report", approval_status: "approve",
          execution_status: "succeeded", timeline: [{
            source: "trace", event_type: "agent", name: "DiagnosisAgent",
            status: "succeeded", timestamp: "2026-07-19T08:00:00Z",
            latency_ms: 20, error_code: null,
          }], timeline_next_cursor: null,
        });
      }
      if (url.includes("/incidents")) return jsonResponse({ items: [{
        incident_id: "INC-1", status: "succeeded", risk: "high",
        started_at: null, updated_at: null, run_count: 1, span_count: 1,
      }], next_cursor: null });
      throw new Error(`unexpected request: ${url}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await authenticate(user);
    await user.click(screen.getByRole("button", { name: "Incidents" }));
    await user.click(await screen.findByRole("button", { name: "View INC-1 details" }));
    expect(await screen.findByText("Unable to load console data")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("DiagnosisAgent")).toBeInTheDocument();
    expect(detailAttempts).toBe(2);
  });

  it("does not let an older detail response replace a newer incident", async () => {
    let resolveFirst!: (response: Response) => void;
    const firstDetail = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.includes("/incidents/INC-1")) return firstDetail;
      if (url.includes("/incidents/INC-2")) return jsonResponse({
        incident_id: "INC-2", status: "succeeded", risk: "low",
        current_stage: "report", approval_status: "not_required",
        execution_status: "succeeded", timeline: [{ source: "trace",
          event_type: "agent", name: "NewerAgent", status: "succeeded",
          timestamp: "2026-07-19T08:02:00Z", latency_ms: 10, error_code: null }],
        timeline_next_cursor: null,
      });
      if (url.includes("/incidents")) return jsonResponse({ items: ["INC-1", "INC-2"].map((id) => ({
        incident_id: id, status: "succeeded", risk: "low", started_at: null,
        updated_at: null, run_count: 1, span_count: 1,
      })), next_cursor: null });
      throw new Error(`unexpected request: ${url}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await authenticate(user);
    await user.click(screen.getByRole("button", { name: "Incidents" }));
    await user.click(await screen.findByRole("button", { name: "View INC-1 details" }));
    await user.click(screen.getByRole("button", { name: "View INC-2 details" }));
    expect(await screen.findByText("NewerAgent")).toBeInTheDocument();

    await act(async () => {
      resolveFirst(jsonResponse({
        incident_id: "INC-1", status: "failed", risk: "high", current_stage: "diagnosis",
        approval_status: null, execution_status: "failed", timeline: [{ source: "trace",
          event_type: "agent", name: "OlderAgent", status: "failed",
          timestamp: "2026-07-19T08:01:00Z", latency_ms: 100, error_code: "OLD" }],
        timeline_next_cursor: null,
      }));
      await firstDetail;
    });
    expect(screen.queryByText("OlderAgent")).not.toBeInTheDocument();
    expect(screen.getByText("NewerAgent")).toBeInTheDocument();
  });
});
