import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageDirectory = path.dirname(fileURLToPath(import.meta.url));
const workspacePackageDirectory = path.dirname(packageDirectory);
const outputFile = path.join(workspacePackageDirectory, "src/generated/schema.d.ts");
const temporaryDirectory = mkdtempSync(path.join(os.tmpdir(), "legacy-api-client-"));
const temporaryOutput = path.join(temporaryDirectory, "schema.d.ts");

try {
  execFileSync(
    "openapi-typescript",
    ["../contracts/openapi.yaml", "--output", temporaryOutput],
    { cwd: workspacePackageDirectory, stdio: "inherit" },
  );

  const generated = readFileSync(temporaryOutput, "utf8");
  const committed = readFileSync(outputFile, "utf8");

  if (generated !== committed) {
    console.error("Generated API types are stale. Run `pnpm gen` and commit the result.");
    process.exitCode = 1;
  }
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
