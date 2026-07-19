import { readdirSync, readFileSync, statSync } from "node:fs";
import { describe, expect, it } from "vitest";

function productionSources(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = `${directory}/${name}`;
    if (statSync(path).isDirectory()) return productionSources(path);
    if (!/\.(ts|tsx)$/.test(name) || name.includes(".test.")) return [];
    return [readFileSync(path, "utf8")];
  });
}

describe("credential storage boundary", () => {
  it("does not use browser persistence APIs", () => {
    const root = `${process.cwd()}/src`;
    const source = productionSources(root).join("\n");
    const forbidden = ["local" + "Storage", "session" + "Storage", "document." + "cookie"];
    for (const api of forbidden) expect(source).not.toContain(api);
  });
});
