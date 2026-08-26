import { rmSync } from "node:fs";

// Keep the package-manager guard usable from PowerShell, cmd.exe, and POSIX
// shells. The previous sh -c wrapper made a normal Windows install depend on
// Git Bash/MSYS2 even though the project itself is Node-based.
rmSync("package-lock.json", { force: true });
rmSync("yarn.lock", { force: true });

const userAgent =
  process.env.npm_config_user_agent ?? process.env.NPM_CONFIG_USER_AGENT ?? "";

if (!userAgent.startsWith("pnpm/")) {
  console.error("Use pnpm instead");
  process.exit(1);
}