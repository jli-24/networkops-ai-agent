import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const consoleRoot = process.cwd();
const repositoryRoot = resolve(consoleRoot, "..");

function read(path: string): string {
  return readFileSync(resolve(repositoryRoot, path), "utf8");
}

describe("Operator Console production image", () => {
  it("builds React assets and runs nginx as a non-root user", () => {
    const dockerfile = read("console/Dockerfile");

    expect(dockerfile).toMatch(/^FROM node:22-alpine AS builder/m);
    expect(dockerfile).toContain("RUN npm ci");
    expect(dockerfile).toContain("RUN npm run build");
    expect(dockerfile).toMatch(/^FROM nginx:1\.29\.5-alpine3\.23 AS runtime/m);
    expect(dockerfile).toContain("USER nginx");
    expect(dockerfile).toContain("HEALTHCHECK");
    expect(dockerfile).toContain("http://127.0.0.1:8080/console-health");
  });

  it("serves the SPA while preserving API, SSE, health, and metrics boundaries", () => {
    const nginx = read("console/nginx.conf");

    expect(nginx).toContain("listen 8080;");
    expect(nginx).toContain("pid /tmp/nginx.pid;");
    expect(nginx).toMatch(/location \/ \{[\s\S]*try_files \$uri \$uri\/ \/index\.html;/);
    expect(nginx).toMatch(/location \/api\/ \{[\s\S]*proxy_pass http:\/\/network_agent_api;/);
    expect(nginx).toContain("proxy_http_version 1.1;");
    expect(nginx).toContain("proxy_read_timeout 3600s;");
    expect(nginx).toContain("proxy_buffering off;");
    expect(nginx).toContain("proxy_cache off;");
    expect(nginx).toContain("add_header X-Accel-Buffering no always;");
    expect(nginx).toMatch(/location = \/metrics \{\s*return 404;/);
    expect(nginx).toMatch(/location = \/health \{[\s\S]*proxy_pass http:\/\/network_agent_api;/);
  });
});

describe("Operator Console Compose service", () => {
  it("replaces only the legacy gateway with the API-dependent console", () => {
    const compose = read("docker-compose.yml");
    const service = compose.match(/  network-agent-console:\r?\n([\s\S]*?)(?=\r?\n  network-agent-api:)/)?.[1];

    expect(compose).not.toMatch(/^  nginx:/m);
    expect(service).toBeDefined();
    expect(service).toContain("context: ./console");
    expect(service).toContain("dockerfile: Dockerfile");
    expect(service).toContain("network-agent-api:");
    expect(service).toContain("condition: service_healthy");
    expect(service).toContain('127.0.0.1:${NETWORKOPS_GATEWAY_PORT:-8080}:8080');
    expect(service).not.toMatch(/postgres:|redis:|prometheus:|grafana:/);
  });
});
