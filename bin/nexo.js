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

function resolveNexoHome(rawValue) {
  const homeDir = os.homedir();
  const managedHome = path.join(homeDir, ".nexo");
  const legacyHome = path.join(homeDir, "claude");
  const candidate = rawValue || managedHome;

  if (candidate === managedHome) return managedHome;
  if (candidate === legacyHome) return fs.existsSync(managedHome) ? managedHome : candidate;

  try {
    if (fs.existsSync(managedHome) && fs.realpathSync.native(candidate) === fs.realpathSync.native(managedHome)) {
      return managedHome;
    }
  } catch {}

  return candidate;
}

const NEXO_HOME = resolveNexoHome(process.env.NEXO_HOME);
const NEXO_CODE = resolveCodeDir();

function resolveCodeDir() {
  const envCode = process.env.NEXO_CODE;
  if (envCode && fs.existsSync(path.join(envCode, "cli.py"))) {
    return envCode;
  }
  const repoCandidate = path.join(__dirname, "..", "src", "cli.py");
  if (fs.existsSync(repoCandidate)) {
    return path.join(__dirname, "..", "src");
  }
  if (fs.existsSync(path.join(NEXO_HOME, "core", "cli.py"))) {
    return path.join(NEXO_HOME, "core");
  }
  if (fs.existsSync(path.join(NEXO_HOME, "cli.py"))) {
    return NEXO_HOME;
  }
  if (fs.existsSync(path.join(NEXO_HOME, "claude", "cli.py"))) {
    return path.join(NEXO_HOME, "claude");
  }
  if (fs.existsSync(path.join(NEXO_HOME, "core"))) {
    return path.join(NEXO_HOME, "core");
  }
  return NEXO_HOME;
}

function pythonSupportsModule(candidate, moduleName) {
  if (!candidate) return false;
  if (candidate.includes("/") && !fs.existsSync(candidate)) return false;
  try {
    const result = spawnSync(candidate, ["-c", `import ${moduleName}`], {
      stdio: "ignore",
      env: {
        ...process.env,
        NEXO_HOME,
        NEXO_CODE,
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
    path.join(NEXO_CODE, ".venv", "bin", "python3"),
    path.join(NEXO_CODE, ".venv", "bin", "python"),
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
  return path.join(NEXO_CODE, "cli.py");
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
    NEXO_CODE,
  },
});

process.exit(result.status ?? 1);
