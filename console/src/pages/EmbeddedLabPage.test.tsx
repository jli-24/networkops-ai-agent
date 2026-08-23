import { render, screen, waitFor } from "@testing-library/react";
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
  active_incidents: 0,
  high_risk_operations: 0,
  policy_denied_count: 0,
  agent_success_rate: 1,
  rca_accuracy: null,
  workflow_latency_ms: null,
};

const taskSummary = {
  task_id: "emb-000001",
  goal: "ESP32 温湿度节点",
  status: "AWAITING_APPROVAL",
  validation_state: "GENERATED",
  summary: null,
  error: null,
  final_report: null,
};

const taskDetail = {
  ...taskSummary,
  artifacts: [
    {
      artifact_id: "art-1",
      type: "hardware_design",
      name: "hardware_design.json",
      version: "1",
      created_by: "HardwareAgent",
      sha256: "a".repeat(64),
      size_bytes: 512,
    },
  ],
  approval_request: {
    request_id: "appr-1",
    action: "simulation_run",
    target: "virtual:in_process",
    risk_level: "low",
    artifact_ref: "main.c",
    execution_plan: [
      "compile firmware via esp32_compile capability",
      "flash to virtual device",
    ],
  },
  firmware_filename: "main.c",
};

const capabilities = [
  {
    name: "esp32_compile",
    version: "2.0",
    type: "firmware",
    permission: "EMBEDDED_SIMULATE",
    backend: "in_process",
    enabled: true,
    metadata: { framework: "ESP-IDF" },
  },
];

describe("EmbeddedLabPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("creates a task, shows pending approval with plan, and approves it", async () => {
    const user = userEvent.setup();
    let tasks: unknown[] = [];
    let approved = false;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.endsWith("/capabilities")) return jsonResponse(capabilities);
      if (url.includes("/embedded/tasks/emb-000001/approval") && method === "POST") {
        approved = true;
        tasks = [{ ...taskSummary, status: "COMPLETED", validation_state: "PASSED", final_report: "任务结束：PASSED" }];
        return jsonResponse({ ...taskSummary, status: "COMPLETED", validation_state: "PASSED", final_report: "任务结束：PASSED" });
      }
      if (url.endsWith("/embedded/tasks") && method === "POST") {
        tasks = [taskSummary];
        return new Response(JSON.stringify(taskSummary), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/embedded/tasks")) return jsonResponse(tasks);
      if (url.includes("/embedded/tasks/emb-000001")) {
        return jsonResponse(
          approved
            ? {
                ...taskDetail,
                status: "COMPLETED",
                validation_state: "PASSED",
                final_report: "任务结束：PASSED",
                approval_request: null,
              }
            : taskDetail,
        );
      }
      return jsonResponse({}, 404);
    });

    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "jwt-value");
    await user.click(screen.getByRole("button", { name: "Connect console" }));
    await user.click(screen.getByRole("button", { name: "Embedded Lab" }));

    await user.type(screen.getByLabelText("Design goal"), "ESP32 温湿度节点");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect((await screen.findAllByText("emb-000001")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Approval request")).toBeInTheDocument();
    expect(screen.getByText("compile firmware via esp32_compile capability")).toBeInTheDocument();
    expect(screen.getByText("hardware_design.json")).toBeInTheDocument();
    expect(screen.getByText(`${"a".repeat(12)}…`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/embedded/tasks/emb-000001/approval",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "approve", actor: "console-operator" }) }),
      ),
    );
    expect(await screen.findByText("任务结束：PASSED")).toBeInTheDocument();
  });

  it("rejects a pending approval", async () => {
    const user = userEvent.setup();
    let rejected = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.endsWith("/capabilities")) return jsonResponse([]);
      if (url.endsWith("/embedded/tasks")) {
        if (method === "POST") return new Response(JSON.stringify(taskSummary), { status: 201, headers: { "Content-Type": "application/json" } });
        return jsonResponse([taskSummary]);
      }
      if (url.includes("/approval") && method === "POST") {
        rejected = true;
        return jsonResponse({ ...taskSummary, status: "FAILED", error: "APPROVAL_REJECTED: rejected" });
      }
      if (url.includes("/embedded/tasks/emb-000001")) {
        return jsonResponse(
          rejected
            ? { ...taskDetail, status: "FAILED", error: "APPROVAL_REJECTED: rejected", approval_request: null }
            : taskDetail,
        );
      }
      return jsonResponse({}, 404);
    });

    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "jwt-value");
    await user.click(screen.getByRole("button", { name: "Connect console" }));
    await user.click(screen.getByRole("button", { name: "Embedded Lab" }));
    await user.type(screen.getByLabelText("Design goal"), "ESP32");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    expect(await screen.findByText("Approval request")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText(/APPROVAL_REJECTED/)).toBeInTheDocument();
  });

  it("surfaces empty state when no tasks exist", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/dashboard")) return jsonResponse(dashboard);
      if (url.endsWith("/capabilities")) return jsonResponse([]);
      if (url.endsWith("/embedded/tasks")) return jsonResponse([]);
      return jsonResponse({}, 404);
    });

    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "jwt-value");
    await user.click(screen.getByRole("button", { name: "Connect console" }));
    await user.click(screen.getByRole("button", { name: "Embedded Lab" }));
    expect(await screen.findByText("No embedded tasks yet.")).toBeInTheDocument();
  });
});
