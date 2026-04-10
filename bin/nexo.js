#!/usr/bin/env node
/**
 * nexo — Runtime operational CLI for NEXO.
 *
 * Thin Node launcher that resolves NEXO_HOME, finds Python,
 * and delegates to src/cli.py (repo mode) or NEXO_HOME/cli.py (installed mode).
 *
 * Business logic lives in Python, not here.
 */
const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const NEXO_HOME = process.env.NEXO_HOME || path.join(os.homedir(), ".nexo");

function pythonSupportsModule(candidate, moduleName) {
  if (!candidate) return false;
  if (candidate.includes("/") && !fs.existsSync(candidate)) return false;
  try {
    const result = spawnSync(candidate, ["-c", `import ${moduleName}`], {
      stdio: "ignore",
      env: {
        ...process.env,
        NEXO_HOME,
        NEXO_CODE: path.join(__dirname, "..", "src"),
      },
    });
    return result.status === 0;
  } catch {
    return false;
  }
}

function findPython() {
  const candidates = [
    process.env.NEXO_RUNTIME_PYTHON,
    process.env.NEXO_PYTHON,
    path.join(NEXO_HOME, ".venv", "bin", "python3"),
    path.join(NEXO_HOME, ".venv", "bin", "python"),
    process.platform === "darwin" ? "/opt/homebrew/bin/python3" : "",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
    "python3",
    "python",
  ];
  let fallback = "";
  for (const c of candidates) {
    if (!c) continue;
    if (!(c.includes("/") ? fs.existsSync(c) : true)) continue;
    if (!fallback) fallback = c;
    if (pythonSupportsModule(c, "fastmcp")) return c;
  }
  return fallback || "python3";
}

function findCliPy() {
  const repoCandidate = path.join(__dirname, "..", "src", "cli.py");
  const installedCandidate = path.join(NEXO_HOME, "cli.py");
  if (fs.existsSync(repoCandidate)) return repoCandidate;
  return installedCandidate;
}

const python = findPython();
const cliPy = findCliPy();

if (!fs.existsSync(cliPy)) {
  console.error(`NEXO CLI not found at ${cliPy}`);
  console.error("Run 'nexo-brain' first to complete installation.");
  process.exit(1);
}

const result = spawnSync(python, [cliPy, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: {
    ...process.env,
    NEXO_HOME,
    NEXO_CODE: path.join(__dirname, "..", "src"),
  },
});

process.exit(result.status ?? 1);
