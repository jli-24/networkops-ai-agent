import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const dashboard = {
  active_incidents: 3,
  high_risk_operations: 2,
  policy_denied_count: 1,
  agent_success_rate: 0.975,
  rca_accuracy: 0.86,
  workflow_latency_ms: 128.4,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Operator Console authentication", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("keeps the token in memory and sends the Bearer header", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(dashboard));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "jwt-value");
    await user.click(screen.getByRole("button", { name: "Connect console" }));

    await screen.findByText("97.5%");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/console/dashboard",
      expect.objectContaining({
        credentials: "omit",
        headers: { Authorization: "Bearer jwt-value" },
      }),
    );
  });

  it("shows a loading state while the dashboard request is pending", async () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => undefined));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "jwt-value");
    await user.click(screen.getByRole("button", { name: "Connect console" }));

    expect(screen.getByText("Loading dashboard…")).toBeInTheDocument();
  });

  it("clears the memory token and returns to authentication after 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Unauthorized" }, 401),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "expired-token");
    await user.click(screen.getByRole("button", { name: "Connect console" }));

    expect(await screen.findByText("Authentication required")).toBeInTheDocument();
    expect(screen.getByLabelText("JWT access token")).toHaveValue("");
  });

  it("keeps the session and shows permission denied after 403", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ detail: "Forbidden" }, 403));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "limited-token");
    await user.click(screen.getByRole("button", { name: "Connect console" }));

    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.queryByLabelText("JWT access token")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        headers: { Authorization: "Bearer limited-token" },
      }),
    );
  });

  it("does not let a stale 401 clear a newer in-memory token", async () => {
    let resolveOld!: (response: Response) => void;
    const oldRequest = new Promise<Response>((resolve) => { resolveOld = resolve; });
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockReturnValueOnce(oldRequest)
      .mockResolvedValueOnce(jsonResponse(dashboard));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("JWT access token"), "old-token");
    await user.click(screen.getByRole("button", { name: "Connect console" }));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    await user.type(screen.getByLabelText("JWT access token"), "new-token");
    await user.click(screen.getByRole("button", { name: "Connect console" }));
    await screen.findByText("97.5%");

    resolveOld(jsonResponse({ detail: "Unauthorized" }, 401));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByLabelText("JWT access token")).not.toBeInTheDocument();
    expect(screen.getByText("97.5%")).toBeInTheDocument();
  });
});
