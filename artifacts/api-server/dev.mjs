import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const artifactDir = path.dirname(fileURLToPath(import.meta.url));

function runNode(args, env = process.env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, {
      cwd: artifactDir,
      env,
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`node ${args.join(" ")} exited with ${signal ?? code}`));
    });
  });
}

await runNode(["./build.mjs"]);

const server = spawn(process.execPath, ["--enable-source-maps", "./dist/index.mjs"], {
  cwd: artifactDir,
  env: { ...process.env, NODE_ENV: "development" },
  stdio: "inherit",
});

const stopServer = (signal) => {
  if (!server.killed) server.kill(signal);
};
process.once("SIGINT", () => stopServer("SIGINT"));
process.once("SIGTERM", () => stopServer("SIGTERM"));

server.once("error", (error) => {
  console.error(error);
  process.exitCode = 1;
});
server.once("exit", (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});