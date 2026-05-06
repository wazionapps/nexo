#!/usr/bin/env node
/**
 * nexo-brain — Interactive installer for NEXO cognitive co-operator.
 *
 * Usage: npx nexo-brain
 *
 * What it does:
 * 1. Asks for the co-operator's name
 * 2. Asks permission to scan the workspace
 * 3. Installs Python dependencies (fastembed, numpy, mcp)
 * 4. Creates ~/.nexo/ with DB, personality, and config
 * 5. Configures Claude Code MCP settings
 * 6. Creates LaunchAgents (macOS) / systemd timers (Linux) / crontab (fallback) for automated processes
 * 7. Generates CLAUDE.md with the operator's instructions
 */

const { execSync, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const { createRequire } = require("module");
const path = require("path");
const readline = require("readline");
// Force relative launcher helpers to resolve from bin/ even under test harnesses.
require = createRequire(path.join(__dirname, "nexo-brain.js"));
const { runViaWsl } = require("./windows-wsl-bridge");

if (process.platform === "win32") {
  const bridged = runViaWsl({
    scriptPath: __filename,
    args: process.argv.slice(2),
    label: "NEXO Brain",
  });
  process.exit(bridged?.status ?? 1);
}

let NEXO_HOME = process.env.NEXO_HOME || path.join(require("os").homedir(), ".nexo");
const DEFAULT_ASSISTANT_NAME = "Nova";
const RESERVED_ASSISTANT_NAME_KEYS = new Set(["nexo", "nexobrain", "nexodesktop"]);
const MIN_INSTALLER_PYTHON_MAJOR = 3;
const MIN_INSTALLER_PYTHON_MINOR = 10;

function normalizeAssistantNameCandidate(value) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function isReservedAssistantName(value) {
  const normalized = normalizeAssistantNameCandidate(value);
  if (!normalized) return false;
  for (const reserved of RESERVED_ASSISTANT_NAME_KEYS) {
    if (normalized === reserved || normalized.includes(reserved)) {
      return true;
    }
  }
  return false;
}

function shouldSkipShellProfileBackfill() {
  // Mirror of _should_skip_shell_profile_backfill() in src/auto_update.py.
  // Prevent the installer from leaking ``export PATH`` / operator alias lines
  // into the developer's real shell profile whenever NEXO_HOME is not the
  // canonical $HOME/.nexo path (pytest tmp dirs, CI sandboxes, containers).
  const flag = String(process.env.NEXO_SKIP_SHELL_PROFILE || "").trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(flag)) {
    return { skip: true, reason: `NEXO_SKIP_SHELL_PROFILE=${flag}` };
  }
  const canonical = path.join(require("os").homedir(), ".nexo");
  let actual = NEXO_HOME;
  try {
    actual = path.resolve(NEXO_HOME);
  } catch {}
  if (actual !== canonical) {
    return { skip: true, reason: `NEXO_HOME=${actual} is not the canonical ${canonical}` };
  }
  return { skip: false, reason: "" };
}

const CLAUDE_SETTINGS = path.join(
  require("os").homedir(),
  ".claude",
  "settings.json"
);
const LAUNCH_AGENTS = path.join(
  require("os").homedir(),
  "Library",
  "LaunchAgents"
);
const MACOS_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles";
const PUBLIC_CONTRIBUTION_UPSTREAM = "wazionapps/nexo";
// Model defaults loaded from src/model_defaults.json — single source of truth
// shared with the Python runtime (src/model_defaults.py). Edit the JSON to
// change the recommended default for new installs and to offer an upgrade
// prompt to existing users via `nexo update`.
const MODEL_DEFAULTS_PATH = path.join(__dirname, "..", "src", "model_defaults.json");
function _loadModelDefaults() {
  const fallback = {
    claude_code: { model: "claude-opus-4-6[1m]", reasoning_effort: "", display_name: "Opus 4.6 with 1M context" },
    codex: { model: "gpt-5.4", reasoning_effort: "xhigh", display_name: "GPT-5.4 with max reasoning" },
  };
  try {
    const raw = JSON.parse(fs.readFileSync(MODEL_DEFAULTS_PATH, "utf8"));
    if (raw && typeof raw === "object") {
      return {
        claude_code: { ...fallback.claude_code, ...(raw.claude_code || {}) },
        codex: { ...fallback.codex, ...(raw.codex || {}) },
      };
    }
  } catch (_) {}
  return fallback;
}
const _MODEL_DEFAULTS = _loadModelDefaults();
const DEFAULT_CLAUDE_CODE_MODEL = _MODEL_DEFAULTS.claude_code.model;
const DEFAULT_CLAUDE_CODE_REASONING_EFFORT = _MODEL_DEFAULTS.claude_code.reasoning_effort || "";
const DEFAULT_CODEX_MODEL = _MODEL_DEFAULTS.codex.model;
const DEFAULT_CODEX_REASONING_EFFORT = _MODEL_DEFAULTS.codex.reasoning_effort || "";

function isDesktopManagedInstall() {
  return String(process.env.NEXO_DESKTOP_MANAGED || "").trim() === "1";
}

// v6.0.0 — Hook manifest is the single source of truth for which hook
// handlers get registered. Both plugin mode (hooks/hooks.json) and npm
// mode (this installer's registerAllCoreHooks) read from the same file.
const HOOKS_MANIFEST_PATH = path.join(__dirname, "..", "src", "hooks", "manifest.json");
function _loadHooksManifest() {
  try {
    const raw = JSON.parse(fs.readFileSync(HOOKS_MANIFEST_PATH, "utf8"));
    if (raw && Array.isArray(raw.hooks)) {
      return raw;
    }
  } catch (_) {}
  return { version: "1.0", hooks: [] };
}
const _HOOKS_MANIFEST = _loadHooksManifest();

// v6.0.0 — Resonance tiers JSON holds the (tier → backend → model+effort)
// mapping. The installer only reads ``default_tier`` and ``tiers`` keys;
// the real resolution happens on the Python side via resonance_map.py.
const RESONANCE_TIERS_PATH = path.join(__dirname, "..", "src", "resonance_tiers.json");
function _loadResonanceTiers() {
  try {
    const raw = JSON.parse(fs.readFileSync(RESONANCE_TIERS_PATH, "utf8"));
    if (raw && raw.tiers && typeof raw.tiers === "object") {
      return raw;
    }
  } catch (_) {}
  return { tiers: {}, default_tier: "alto" };
}
const _RESONANCE_TIERS = _loadResonanceTiers();
const RESONANCE_TIER_NAMES = ["maximo", "alto", "medio", "bajo"];
const DEFAULT_RESONANCE_TIER = _RESONANCE_TIERS.default_tier || "alto";

function isEphemeralInstall(nexoHome) {
  const os = require("os");
  const homeDir = os.homedir();
  const allowEphemeral = process.env.NEXO_ALLOW_EPHEMERAL_INSTALL === "1";
  if (allowEphemeral) return false;

  const normalize = (candidate) => {
    if (!candidate) return "";
    let resolved = String(candidate);
    try {
      resolved = fs.realpathSync.native(resolved);
    } catch {
      try {
        resolved = fs.realpathSync(resolved);
      } catch {
        resolved = path.resolve(resolved);
      }
    }
    return resolved.replace(/\\/g, "/").replace(/\/+$/, "");
  };

  const tempRoots = new Set();
  for (const root of [os.tmpdir(), "/tmp", "/var/folders", "/private/var/folders"]) {
    const normalized = normalize(root);
    if (!normalized) continue;
    tempRoots.add(normalized);
    if (normalized === "/tmp") {
      tempRoots.add("/private/tmp");
    } else if (normalized === "/private/tmp") {
      tempRoots.add("/tmp");
    } else if (normalized.startsWith("/var/")) {
      tempRoots.add(`/private${normalized}`);
    } else if (normalized.startsWith("/private/var/")) {
      tempRoots.add(normalized.replace(/^\/private/, ""));
    }
  }

  const isWithin = (candidate, root) => (
    candidate === root || candidate.startsWith(`${root}/`)
  );

  return [nexoHome, homeDir]
    .map(normalize)
    .filter(Boolean)
    .some((candidate) => Array.from(tempRoots).some((root) => isWithin(candidate, root)));
}

let rl = null;

function getReadline() {
  if (!rl) {
    rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
  }
  return rl;
}

function ask(question) {
  return new Promise((resolve) => getReadline().question(question, resolve));
}

function closeReadline() {
  if (rl) {
    rl.close();
    rl = null;
  }
}

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, { encoding: "utf8", stdio: "pipe", ...opts }).trim();
  } catch {
    return null;
  }
}

function shSingleQuote(value) {
  return "'" + String(value || "").replace(/'/g, "'\\''") + "'";
}

function runPythonProbe(pythonBin, args, timeout = 15000) {
  if (!pythonBin) return null;
  try {
    const result = spawnSync(pythonBin, args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout,
    });
    if (result.status !== 0) return null;
    return String(result.stdout || result.stderr || "").trim();
  } catch {
    return null;
  }
}

function pythonVersion(pythonBin) {
  return runPythonProbe(pythonBin, ["-c", "import sys; print(sys.version.split()[0])"]);
}

function pythonVersionMeetsMinimum(versionText) {
  const match = String(versionText || "").trim().match(/^(\d+)\.(\d+)(?:\.|$)/);
  if (!match) return false;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  return major > MIN_INSTALLER_PYTHON_MAJOR
    || (major === MIN_INSTALLER_PYTHON_MAJOR && minor >= MIN_INSTALLER_PYTHON_MINOR);
}

function resolveInstallerPython() {
  const candidates = [
    process.env.NEXO_BOOTSTRAP_PYTHON,
    process.env.NEXO_RUNTIME_PYTHON,
    process.env.NEXO_PYTHON,
    run("which python3"),
    run("which python"),
  ].filter(Boolean);
  const seen = new Set();
  for (const candidate of candidates) {
    const clean = String(candidate || "").trim();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    const version = pythonVersion(clean);
    if (version && pythonVersionMeetsMinimum(version)) return clean;
  }
  return "";
}

function findBundledWheel(wheelsDir, prefix) {
  try {
    const normalizedPrefix = String(prefix || "").toLowerCase() + "-";
    const matches = fs.readdirSync(wheelsDir)
      .filter((name) => name.toLowerCase().startsWith(normalizedPrefix) && name.endsWith(".whl"))
      .sort();
    if (!matches.length) return "";
    return path.join(wheelsDir, matches[matches.length - 1]);
  } catch {
    return "";
  }
}

function bundledWheelsSupportCurrentPlatform(wheelsDir) {
  if (!fs.existsSync(wheelsDir)) return false;
  if (process.platform === "linux") return true;
  if (process.platform !== "darwin") return false;
  try {
    const names = fs.readdirSync(wheelsDir).map((name) => String(name || "").toLowerCase());
    const archTag = process.arch === "arm64" ? "arm64" : "x86_64";
    return names.some((name) => (
      name.endsWith(".whl")
      && name.includes("macosx")
      && (name.includes("universal2") || name.includes(archTag))
    ));
  } catch {
    return false;
  }
}

function pythonHasPip(pythonBin) {
  try {
    const result = spawnSync(pythonBin, ["-m", "pip", "--version"], {
      stdio: "ignore",
      timeout: 15000,
    });
    return result.status === 0;
  } catch {
    return false;
  }
}

function managedVenvPythonPath(nexoHome = NEXO_HOME) {
  const venvPath = path.join(nexoHome, ".venv");
  return process.platform === "win32"
    ? path.join(venvPath, "Scripts", "python.exe")
    : path.join(venvPath, "bin", "python3");
}

function safeTimestampForPath() {
  return new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
}

function uniqueBackupPath(targetPath, suffix) {
  const dir = path.dirname(targetPath);
  const base = path.basename(targetPath);
  const stamp = safeTimestampForPath();
  let candidate = path.join(dir, `${base}.${suffix}-${stamp}`);
  if (!fs.existsSync(candidate)) return candidate;
  for (let i = 2; i < 100; i += 1) {
    candidate = path.join(dir, `${base}.${suffix}-${stamp}-${i}`);
    if (!fs.existsSync(candidate)) return candidate;
  }
  return path.join(dir, `${base}.${suffix}-${stamp}-${process.pid}`);
}

function ensureManagedVenvCompatible(venvPath, venvPython) {
  if (!fs.existsSync(venvPython)) return;
  const version = pythonVersion(venvPython);
  if (version && pythonVersionMeetsMinimum(version)) return;

  const reason = version ? `Python ${version}` : "an unreadable Python executable";
  const backupPath = uniqueBackupPath(venvPath, "unsupported-python");
  log(`  Existing Python virtual environment uses ${reason}; moving it aside to recreate.`);
  try {
    fs.renameSync(venvPath, backupPath);
  } catch (err) {
    throw new Error(`Existing NEXO Python virtual environment is incompatible and could not be moved aside: ${err.message || err}`);
  }
  log(`  Previous Python virtual environment moved to ${backupPath}`);
}

function seedPipFromBundledWheels(venvPython, bundledWheelsDir) {
  if (!fs.existsSync(venvPython) || !fs.existsSync(bundledWheelsDir)) return false;
  if (pythonHasPip(venvPython)) return true;
  const pipWheel = findBundledWheel(bundledWheelsDir, "pip");
  if (!pipWheel) return false;
  log("  Seeding pip into venv from bundled wheels...");
  const result = spawnSync(venvPython, [
    path.join(pipWheel, "pip"),
    "install",
    "--no-index",
    "--find-links",
    bundledWheelsDir,
    "pip",
    "setuptools",
    "wheel",
  ], { stdio: "inherit", timeout: 120000 });
  return result.status === 0 && pythonHasPip(venvPython);
}

function log(msg) {
  console.log(`  ${msg}`);
  try {
    const logPath = path.join(
      NEXO_HOME,
      "runtime",
      "bootstrap",
      "logs",
      "brain-install.log"
    );
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] ${msg}\n`);
  } catch {}
}

let _stagedRuntimeBundleRoot = null;
let _stagedRuntimeCleanup = null;

function getRuntimeBundleRoot() {
  if (_stagedRuntimeBundleRoot) {
    return _stagedRuntimeBundleRoot;
  }
  const bundleRoot = path.resolve(__dirname, "..");
  if (process.platform !== "linux") {
    return bundleRoot;
  }
  const normalizedRoot = bundleRoot.replace(/\\/g, "/");
  if (!normalizedRoot.startsWith("/mnt/")) {
    return bundleRoot;
  }

  const tmpRoot = fs.mkdtempSync(path.join(require("os").tmpdir(), "nexo-brain-stage-"));
  const stagedRoot = path.join(tmpRoot, path.basename(bundleRoot));
  fs.mkdirSync(stagedRoot, { recursive: true });

  log("Staging bundled runtime into local Linux storage...");
  const tarCmd =
    `tar --exclude=__pycache__ --exclude=node_modules --exclude='*.pyc' ` +
    `--exclude='*.db' --exclude=.git ` +
    `-C ${JSON.stringify(bundleRoot)} -cf - . | ` +
    `tar -C ${JSON.stringify(stagedRoot)} -xf -`;
  const stageResult = spawnSync("/bin/sh", ["-c", tarCmd], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 10 * 60 * 1000,
  });
  if (stageResult.status !== 0) {
    const detail = (stageResult.stderr || stageResult.stdout || "").trim() || `exit ${stageResult.status}`;
    throw new Error(`Failed to stage bundled runtime from Windows storage: ${detail}`);
  }
  log(`  Runtime bundle staged at ${stagedRoot}`);
  _stagedRuntimeBundleRoot = stagedRoot;
  _stagedRuntimeCleanup = () => {
    try {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
    } catch {}
  };
  return _stagedRuntimeBundleRoot;
}

function readPackageJson() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
}

function writeJsonAtomic(targetPath, payload) {
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  const tmpPath = path.join(
    path.dirname(targetPath),
    `.${path.basename(targetPath)}.${process.pid}.${Date.now()}.tmp`
  );
  fs.writeFileSync(tmpPath, JSON.stringify(payload, null, 2) + "\n");
  fs.renameSync(tmpPath, targetPath);
}

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function calibrationPathCandidates(nexoHome) {
  return [
    path.join(nexoHome, "personal", "brain", "calibration.json"),
    path.join(nexoHome, "brain", "calibration.json"),
  ];
}

function readRuntimeCalibration(nexoHome = NEXO_HOME) {
  for (const filePath of calibrationPathCandidates(nexoHome)) {
    if (!fs.existsSync(filePath)) continue;
    const payload = readJsonFile(filePath);
    if (payload && typeof payload === "object") {
      return { path: filePath, payload };
    }
  }
  return { path: null, payload: null };
}

function profilePathCandidates(nexoHome = NEXO_HOME) {
  return [
    path.join(nexoHome, "personal", "brain", "profile.json"),
    path.join(nexoHome, "brain", "profile.json"),
  ];
}

function readRuntimeProfile(nexoHome = NEXO_HOME) {
  for (const filePath of profilePathCandidates(nexoHome)) {
    if (!fs.existsSync(filePath)) continue;
    const payload = readJsonFile(filePath);
    if (payload && typeof payload === "object") {
      return { path: filePath, payload };
    }
  }
  return { path: null, payload: null };
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isPlaceholderUserName(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "" || normalized === "usuario";
}

function isOnboardingComplete(calibration) {
  if (!calibration || typeof calibration !== "object") return false;
  const user = calibration.user && typeof calibration.user === "object" ? calibration.user : {};
  const name = user.name;
  const language = user.language || calibration.language;
  const meta = calibration.meta && typeof calibration.meta === "object" ? calibration.meta : {};

  if (meta.onboarding_completed === true) {
    // v7.12.11 — bug surfaced on Inma's smoke install 2026-05-03: Desktop
    // bootstrap runs nexo-brain in --yes/--skip mode, which used to write
    // `onboarding_completed: true` alongside the placeholder "Usuario"/"en"
    // defaults. The Desktop wizard then never fired because this returned
    // true on the very first launch. Treat a placeholder name as "marker is
    // a lie, real onboarding never happened" so the renderer can still
    // surface the wizard. Same guard applied to the legacy fallback below.
    return nonEmptyString(name) && !isPlaceholderUserName(name) && nonEmptyString(language);
  }

  // Legacy fallback: v7.8/v7.9-era calibration files may not carry the
  // current schema marker, but a real operator name + language is enough to
  // prove the setup was completed. Placeholder defaults stay incomplete.
  return nonEmptyString(name) && !isPlaceholderUserName(name) && nonEmptyString(language);
}

function hasPartialPlaceholderCalibration(calibration) {
  if (!calibration || typeof calibration !== "object") return false;
  const meta = calibration.meta && typeof calibration.meta === "object" ? calibration.meta : {};
  if (meta.onboarding_completed === true) return false;
  const user = calibration.user && typeof calibration.user === "object" ? calibration.user : {};
  const name = user.name;
  const language = user.language || calibration.language;
  return isPlaceholderUserName(name) || !nonEmptyString(language);
}

function firstMeaningfulString(...values) {
  for (const value of values) {
    if (typeof value !== "string") continue;
    const clean = value.trim();
    if (clean) return clean;
  }
  return "";
}

function normalizeLanguageCode(value) {
  const clean = String(value || "").trim().toLowerCase().replace("_", "-");
  if (!clean) return "";
  return clean.split("-")[0];
}

function resolveExistingIdentityDefaults(nexoHome = NEXO_HOME) {
  const calibration = readRuntimeCalibration(nexoHome).payload || {};
  const user = calibration.user && typeof calibration.user === "object" ? calibration.user : {};
  const profile = readRuntimeProfile(nexoHome).payload || {};
  const version = readJsonFile(path.join(nexoHome, "version.json")) || {};

  const userName = firstMeaningfulString(
    user.name,
    calibration.user_name,
    profile.user_name,
    version.user_name,
  );
  const language = normalizeLanguageCode(firstMeaningfulString(
    user.language,
    calibration.language,
    profile.language,
    version.language,
  ));
  const operatorName = firstMeaningfulString(
    user.assistant_name,
    calibration.assistant_name,
    calibration.operator_name,
    profile.assistant_name,
    profile.operator_name,
    version.operator_name,
  );

  return {
    userName: !isPlaceholderUserName(userName) ? userName : "",
    language,
    operatorName: !isReservedAssistantName(operatorName) ? operatorName : "",
  };
}

function ensureOnboardingCompletionMarker(nexoHome = NEXO_HOME) {
  const record = readRuntimeCalibration(nexoHome);
  if (!record.path || !isOnboardingComplete(record.payload)) {
    return { changed: false, complete: false, path: record.path };
  }
  const meta = record.payload.meta && typeof record.payload.meta === "object"
    ? { ...record.payload.meta }
    : {};
  if (meta.onboarding_completed === true) {
    return { changed: false, complete: true, path: record.path };
  }
  const next = {
    ...record.payload,
    version: Math.max(Number(record.payload.version) || 1, 2),
    meta: {
      ...meta,
      onboarding_completed: true,
      onboarding_completed_at: meta.onboarding_completed_at || new Date().toISOString(),
      migrated_from_legacy_calibration: true,
    },
  };
  writeJsonAtomic(record.path, next);
  return { changed: true, complete: true, path: record.path };
}

const WARMUP_SCRIPT = path.join(__dirname, "..", "src", "model_warmup.py");
const WARMUP_PIP_PACKAGES = [
  "transformers",
  "torch",
  "sentencepiece",
  "sentence-transformers",
];
const WARMUP_TIMEOUT_MS = 60 * 60 * 1000;

function shouldSkipModelWarmup() {
  const flag = String(process.env.NEXO_SKIP_MODEL_WARMUP || "").trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(flag);
}

function resolveSystemPython() {
  return run("which python3") || run("which python") || "python3";
}

function ensureWarmupPython(nexoHome = NEXO_HOME) {
  const venvPath = path.join(nexoHome, ".venv");
  const venvPython = managedVenvPythonPath(nexoHome);
  fs.mkdirSync(nexoHome, { recursive: true });
  ensureManagedVenvCompatible(venvPath, venvPython);
  if (fs.existsSync(venvPython)) return venvPython;

  const basePython = resolveInstallerPython() || resolveSystemPython();
  if (!fs.existsSync(venvPython)) {
    log("  Creating Python virtual environment for model warmup...");
    const result = spawnSync(basePython, ["-m", "venv", venvPath], { stdio: "inherit", timeout: 120000 });
    if (result.status !== 0) {
      throw new Error("could not create Python virtual environment for model warmup");
    }
  }
  return venvPython;
}

function installWarmupPythonDependencies(pythonPath, { quiet = false, installRuntimeDeps = true } = {}) {
  const requirementsFile = path.join(__dirname, "..", "src", "requirements.txt");
  const pipCommon = ["-m", "pip", "install"];
  if (quiet) pipCommon.push("--quiet");
  const stdio = quiet ? "pipe" : "inherit";

  if (installRuntimeDeps && fs.existsSync(requirementsFile)) {
    const reqResult = spawnSync(
      pythonPath,
      [...pipCommon, "-r", requirementsFile],
      { stdio, timeout: WARMUP_TIMEOUT_MS }
    );
    if (reqResult.status !== 0) {
      throw new Error("failed to install runtime Python dependencies for model warmup");
    }
  }

  const classifierResult = spawnSync(
    pythonPath,
    [...pipCommon, ...WARMUP_PIP_PACKAGES],
    { stdio, timeout: WARMUP_TIMEOUT_MS }
  );
  if (classifierResult.status !== 0) {
    throw new Error("failed to install local classifier dependencies for model warmup");
  }
}

function runModelWarmup(pythonPath, {
  nexoHome = NEXO_HOME,
  dryRun = false,
  json = false,
  strict = true,
  quiet = false,
} = {}) {
  if (!fs.existsSync(WARMUP_SCRIPT)) {
    throw new Error(`model warmup script not found: ${WARMUP_SCRIPT}`);
  }
  const args = [WARMUP_SCRIPT];
  if (dryRun) args.push("--dry-run");
  if (json) args.push("--json");
  if (strict) args.push("--strict");
  const result = spawnSync(pythonPath, args, {
    cwd: path.join(__dirname, "..", "src"),
    env: { ...process.env, NEXO_HOME: nexoHome, NEXO_CODE: path.join(__dirname, "..", "src") },
    stdio: json ? "pipe" : (quiet ? "pipe" : "inherit"),
    encoding: json || quiet ? "utf8" : undefined,
    timeout: WARMUP_TIMEOUT_MS,
  });
  if (json && result.stdout) process.stdout.write(result.stdout);
  if (json && result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    const details = json || quiet
      ? String(result.stderr || result.stdout || "").trim()
      : "";
    throw new Error(details || `model warmup failed with exit ${result.status}`);
  }
}

function runMandatoryModelWarmup(pythonPath, nexoHome = NEXO_HOME, { reason = "install", installRuntimeDeps = true } = {}) {
  if (shouldSkipModelWarmup()) {
    log(`Model warmup skipped by NEXO_SKIP_MODEL_WARMUP during ${reason}.`);
    return;
  }
  log(`Warming up local Brain/Desktop models (${reason})...`);
  installWarmupPythonDependencies(pythonPath, { quiet: false, installRuntimeDeps });
  runModelWarmup(pythonPath, { nexoHome, strict: true });
  log("Local model warmup complete.");
}

function runDesktopAwareModelWarmup(pythonPath, nexoHome = NEXO_HOME, options = {}) {
  const reason = String((options && options.reason) || "install");
  if (isDesktopManagedInstall()) {
    log(`Desktop-managed runtime detected — local model warmup deferred during ${reason}.`);
    return;
  }
  runMandatoryModelWarmup(pythonPath, nexoHome, options);
}

async function runWarmupModelsCommand(args) {
  const dryRun = args.includes("--dry-run");
  const json = args.includes("--json");
  const force = args.includes("--force");
  const quiet = args.includes("--postinstall") || args.includes("--quiet");

  if (shouldSkipModelWarmup() && !force) {
    if (json) {
      process.stdout.write(JSON.stringify({ ok: true, skipped: true, reason: "NEXO_SKIP_MODEL_WARMUP" }) + "\n");
    } else {
      log("Model warmup skipped by NEXO_SKIP_MODEL_WARMUP.");
    }
    return;
  }

  const pythonPath = dryRun ? resolveSystemPython() : ensureWarmupPython(NEXO_HOME);
  if (!dryRun) {
    installWarmupPythonDependencies(pythonPath, { quiet });
  }
  runModelWarmup(pythonPath, {
    nexoHome: NEXO_HOME,
    dryRun,
    json,
    strict: !args.includes("--best-effort"),
    quiet,
  });
}

function printHelp() {
  const version = readPackageJson().version;
  console.log(`nexo-brain ${version}`);
  console.log("");
  console.log("Usage:");
  console.log("  nexo-brain [--yes|--skip|--defaults]");
  console.log("  nexo-brain --version");
  console.log("  nexo-brain warmup-models [--dry-run] [--json] [--force]");
}

async function maybeHandleTopLevelCommand(argv = process.argv.slice(2)) {
  if (argv.includes("--version") || argv.includes("-v") || argv[0] === "version") {
    console.log(`nexo-brain ${readPackageJson().version}`);
    return true;
  }
  if (argv.includes("--help") || argv.includes("-h") || argv[0] === "help") {
    printHelp();
    return true;
  }
  if (argv[0] === "warmup-models") {
    await runWarmupModelsCommand(argv.slice(1));
    return true;
  }

  const setupFlags = new Set(["--defaults", "--yes", "--skip", "-y"]);
  const unknownCommand = argv.find((arg) => !setupFlags.has(arg));
  if (unknownCommand && !unknownCommand.startsWith("-")) {
    console.error(`Unknown nexo-brain command: ${unknownCommand}`);
    console.error("Run 'nexo-brain --help' for usage.");
    process.exitCode = 2;
    return true;
  }
  return false;
}

function duplicateArtifactCanonicalName(name) {
  const ext = path.extname(name);
  const stem = ext ? name.slice(0, -ext.length) : name;
  const match = stem.match(/^(.*) ([2-9]\d*)$/);
  if (!match) return null;
  return `${match[1]}${ext}`;
}

function isDuplicateArtifactName(name, dirPath = "") {
  const canonical = duplicateArtifactCanonicalName(name);
  if (!canonical || !dirPath) return false;
  return fs.existsSync(path.join(dirPath, canonical));
}

function syncWatchdogHashRegistry(nexoHome) {
  try {
    const scriptsDir = runtimeScriptsDir(nexoHome);
    const watchdogPath = path.join(scriptsDir, "nexo-watchdog.sh");
    if (!fs.existsSync(watchdogPath)) return;

    const registryPath = path.join(scriptsDir, ".watchdog-hashes");
    const entries = new Map();
    if (fs.existsSync(registryPath)) {
      for (const line of fs.readFileSync(registryPath, "utf8").split(/\r?\n/)) {
        if (!line.includes("|")) continue;
        const [filePath, expectedHash] = line.split("|");
        if (filePath) entries.set(filePath, expectedHash || "");
      }
    }

    const digest = crypto.createHash("sha256").update(fs.readFileSync(watchdogPath)).digest("hex");
    entries.set(watchdogPath, digest);
    const body = Array.from(entries.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([filePath, hash]) => `${filePath}|${hash}`)
      .join("\n");
    fs.writeFileSync(registryPath, `${body}\n`);
  } catch (err) {
    log(`WARN: could not sync watchdog hash registry: ${err.message}`);
  }
}

function runtimeCodeDir(nexoHome) {
  const coreDir = path.join(nexoHome, "core");
  for (const candidate of [coreDir, nexoHome]) {
    if (
      fs.existsSync(path.join(candidate, "cli.py")) ||
      fs.existsSync(path.join(candidate, "server.py")) ||
      fs.existsSync(path.join(candidate, "db"))
    ) {
      return candidate;
    }
  }
  return fs.existsSync(coreDir) ? coreDir : nexoHome;
}

function runtimeServerPath(nexoHome) {
  const codeDir = runtimeCodeDir(nexoHome);
  if (fs.existsSync(path.join(codeDir, "server.py"))) {
    return path.join(codeDir, "server.py");
  }
  return path.join(nexoHome, "server.py");
}

function runtimeHooksDir(nexoHome) {
  const codeDir = runtimeCodeDir(nexoHome);
  const hooksDir = path.join(codeDir, "hooks");
  return fs.existsSync(hooksDir) ? hooksDir : path.join(nexoHome, "hooks");
}

function runtimeScriptsDir(nexoHome) {
  const codeDir = runtimeCodeDir(nexoHome);
  const scriptsDir = path.join(codeDir, "scripts");
  return fs.existsSync(scriptsDir) ? scriptsDir : path.join(nexoHome, "scripts");
}

function writeRuntimeCoreArtifactsManifest(nexoHome, srcDir) {
  try {
    const listTopLevelFiles = (dirPath) => {
      if (!fs.existsSync(dirPath)) return [];
      return fs.readdirSync(dirPath)
        .filter((name) => {
          const full = path.join(dirPath, name);
          return fs.existsSync(full) && fs.statSync(full).isFile() && !isDuplicateArtifactName(name, dirPath);
        })
        .sort();
    };
    const payload = {
      generated_at: new Date().toISOString(),
      script_names: listTopLevelFiles(path.join(srcDir, "scripts")),
      hook_names: listTopLevelFiles(path.join(srcDir, "hooks")),
    };
    const manifestBody = `${JSON.stringify(payload, null, 2)}\n`;
    const configDirs = [
      path.join(nexoHome, "config"),
      path.join(nexoHome, "personal", "config"),
    ];
    const seen = new Set();
    for (const configDir of configDirs) {
      if (seen.has(configDir)) continue;
      seen.add(configDir);
      fs.mkdirSync(configDir, { recursive: true });
      fs.writeFileSync(path.join(configDir, "runtime-core-artifacts.json"), manifestBody);
    }
  } catch (err) {
    log(`WARN: could not write runtime core-artifacts manifest: ${err.message}`);
  }
}

function syncRuntimePackageMetadata(repoRoot = path.join(__dirname, ".."), runtimeHome = NEXO_HOME) {
  try {
    const pkgSrc = path.join(repoRoot, "package.json");
    if (fs.existsSync(pkgSrc)) {
      fs.copyFileSync(pkgSrc, path.join(runtimeHome, "package.json"));
    }
  } catch (err) {
    log(`WARN: could not sync runtime package metadata: ${err.message}`);
  }
}

function resolveRuntimeConfigDir(nexoHome) {
  const canonical = path.join(nexoHome, "personal", "config");
  const legacy = path.join(nexoHome, "config");
  if (fs.existsSync(canonical)) return canonical;
  if (fs.existsSync(legacy)) return legacy;
  return canonical;
}

function resolveRuntimeBrainDir(nexoHome) {
  const canonical = path.join(nexoHome, "personal", "brain");
  const legacy = path.join(nexoHome, "brain");
  if (fs.existsSync(canonical)) return canonical;
  if (fs.existsSync(legacy)) return legacy;
  return canonical;
}

function resolveRuntimeDataDir(nexoHome) {
  const canonical = path.join(nexoHome, "runtime", "data");
  const legacy = path.join(nexoHome, "data");
  if (fs.existsSync(canonical)) return canonical;
  if (fs.existsSync(legacy)) return legacy;
  return canonical;
}

function resolveRuntimeLogsDir(nexoHome) {
  const canonical = path.join(nexoHome, "runtime", "logs");
  const legacy = path.join(nexoHome, "logs");
  if (fs.existsSync(canonical)) return canonical;
  if (fs.existsSync(legacy)) return legacy;
  return canonical;
}

function resolveRuntimeCronsDir(nexoHome) {
  const canonical = path.join(nexoHome, "runtime", "crons");
  const legacy = path.join(nexoHome, "crons");
  if (fs.existsSync(canonical)) return canonical;
  if (fs.existsSync(legacy)) return legacy;
  return canonical;
}

function resolveRuntimeSchedulePath(nexoHome) {
  return path.join(resolveRuntimeConfigDir(nexoHome), "schedule.json");
}

function finalizeF06Layout(python, nexoHome = NEXO_HOME) {
  try {
    // auto_update lives in NEXO_CODE (e.g. ~/.nexo/core or ~/.nexo/core/current),
    // not directly in NEXO_HOME, so PYTHONPATH must point at the runtime code
    // directory or `import auto_update` fails with ModuleNotFoundError.
    const codeDir = runtimeCodeDir(nexoHome);
    const result = spawnSync(
      python,
      [
        "-c",
        [
          "import auto_update",
          "auto_update._maybe_migrate_to_f06_layout()",
          "auto_update._rewrite_f06_launch_agents()",
        ].join("; "),
      ],
      {
        cwd: nexoHome,
        env: {
          ...process.env,
          NEXO_HOME: nexoHome,
          NEXO_CODE: codeDir,
          PYTHONPATH: codeDir,
        },
        encoding: "utf8",
      },
    );
    if (result.status !== 0) {
      const detail = (result.stderr || result.stdout || "").trim();
      throw new Error(detail || "unknown error");
    }
    const marker = path.join(nexoHome, ".structure-version");
    if (!fs.existsSync(marker) || fs.readFileSync(marker, "utf8").trim() !== "F0.6") {
      throw new Error("F0.6 structure marker missing after layout finalization");
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String((err && err.message) || err) };
  }
}

function readRuntimeVersionFrom(basePath) {
  if (!basePath) return "";
  for (const candidate of [
    path.join(basePath, "version.json"),
    path.join(basePath, "package.json"),
  ]) {
    try {
      if (!fs.existsSync(candidate)) continue;
      const payload = JSON.parse(fs.readFileSync(candidate, "utf8"));
      const version = String(payload.version || "").trim();
      if (version) return version;
    } catch (_) {}
  }
  return "";
}

function readActiveRuntimeSnapshotVersion(nexoHome = NEXO_HOME) {
  return readRuntimeVersionFrom(path.join(nexoHome, "core", "current"));
}

function activateVersionedRuntimeSnapshot(python, nexoHome = NEXO_HOME, version = "") {
  try {
    const srcDir = path.join(__dirname, "..", "src");
    const inline = [
      "import json, os, pathlib, sys",
      `sys.path.insert(0, ${JSON.stringify(srcDir)})`,
      "from runtime_versioning import activate_versioned_runtime_snapshot",
      "home = pathlib.Path(os.environ['NEXO_HOME'])",
      `result = activate_versioned_runtime_snapshot(source_root=home / 'core', version=${JSON.stringify(version)})`,
      "print(json.dumps(result))",
    ].join("; ");
    const result = spawnSync(python, ["-c", inline], {
      cwd: nexoHome,
      env: {
        ...process.env,
        NEXO_HOME: nexoHome,
      },
      encoding: "utf8",
    });
    if (result.status !== 0) {
      const detail = (result.stderr || result.stdout || "").trim();
      throw new Error(detail || "activation command failed");
    }
    const payload = JSON.parse(String(result.stdout || "{}").trim() || "{}");
    if (!payload || payload.ok !== true) {
      throw new Error(payload && payload.error ? payload.error : "activation returned not-ok");
    }
    return payload;
  } catch (err) {
    return { ok: false, error: String((err && err.message) || err) };
  }
}

function getCoreRuntimeFlatFiles(srcDir = path.join(__dirname, "..", "src")) {
  const staticFiles = [
    "server.py",
    "plugin_loader.py",
    "knowledge_graph.py",
    "kg_populate.py",
    "maintenance.py",
    "storage_router.py",
    "claim_graph.py",
    "hnsw_index.py",
    "evolution_cycle.py",
    "migrate_embeddings.py",
    "auto_close_sessions.py",
    "client_sync.py",
    "client_preferences.py",
    "agent_runner.py",
    "bootstrap_docs.py",
    "auto_update.py",
    "db_guard.py",
    "tools_sessions.py",
    "tools_coordination.py",
    "tools_reminders.py",
    "tools_reminders_crud.py",
    "tools_learnings.py",
    "tools_credentials.py",
    "tools_task_history.py",
    "tools_menu.py",
    "cli.py",
    "script_registry.py",
    "skills_runtime.py",
    "user_context.py",
    "public_contribution.py",
    "cron_recovery.py",
    "runtime_power.py",
    "requirements.txt",
    "model_defaults.json",
  ];
  const discoveredRootModules = fs.existsSync(srcDir)
    ? fs.readdirSync(srcDir)
      .filter((name) => {
        if (isDuplicateArtifactName(name, srcDir)) return false;
        const stat = fs.statSync(path.join(srcDir, name));
        if (!stat.isFile()) return false;
        // Include Python modules and flat JSON contracts that the runtime
        // reads directly from the installed core tree.
        return (
          name.endsWith(".py")
          || /(?:_defaults|_manifest|_tiers)\.json$/.test(name)
        );
      })
    : [];
  return [...new Set([...staticFiles, ...discoveredRootModules])];
}

function getCoreRuntimePackages() {
  return ["db", "cognitive", "doctor"];
}

// Brain contracts — files the NEXO Brain publishes to consumers like
// NEXO Desktop under ~/.nexo/brain/. These are NOT code (they live outside
// getCoreRuntimeFlatFiles for that reason) but data contracts with a stable
// path that external clients read. Keep in sync with docs/contracts/.
function publishBrainContracts(srcDir = path.join(__dirname, "..", "src"), nexoHome = NEXO_HOME) {
  const brainDir = resolveRuntimeBrainDir(nexoHome);
  fs.mkdirSync(brainDir, { recursive: true });
  const contracts = ["resonance_tiers.json"];
  contracts.forEach((name) => {
    const src = path.join(srcDir, name);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(brainDir, name));
    }
  });
  // Clean up legacy location: before v6.0.3 the file was written to
  // NEXO_HOME/resonance_tiers.json. Remove it so only the contract path
  // remains authoritative.
  const legacy = path.join(nexoHome, "resonance_tiers.json");
  if (fs.existsSync(legacy)) {
    try { fs.unlinkSync(legacy); } catch (_) { /* best-effort */ }
  }
}

function resolveLaunchAgentPath(home) {
  const parts = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
    path.join(home, ".local/bin"), path.join(home, ".nexo/bin")];
  // Detect nvm node
  const nvmDir = path.join(home, ".nvm/versions/node");
  try {
    const versions = fs.readdirSync(nvmDir)
      .map(v => ({ name: v, mtime: fs.statSync(path.join(nvmDir, v)).mtimeMs }))
      .sort((a, b) => b.mtime - a.mtime);
    for (const v of versions) {
      const nodeBin = path.join(nvmDir, v.name, "bin");
      if (fs.existsSync(path.join(nodeBin, "node"))) {
        parts.unshift(nodeBin);
        break;
      }
    }
  } catch { /* nvm not installed — skip */ }
  return parts.join(":");
}

function setupKeychainPassFile(nexoHome) {
  if (process.platform !== "darwin") return;
  const configDir = resolveRuntimeConfigDir(nexoHome);
  const passFile = path.join(configDir, ".keychain-pass");
  if (fs.existsSync(passFile)) return; // already set up
  fs.mkdirSync(configDir, { recursive: true });
  log("");
  log("macOS Keychain setup for headless automation:");
  log("  Claude Code stores auth in the login Keychain, which auto-locks.");
  log("  Background jobs need to unlock it. Enter your macOS login password");
  log("  (stored locally in ~/.nexo/config/.keychain-pass, chmod 600).");
  log("");
  return new Promise((resolve) => {
    const rl = require("readline").createInterface({ input: process.stdin, output: process.stdout });
    rl.question("  macOS login password (or Enter to skip): ", (answer) => {
      rl.close();
      if (answer && answer.trim()) {
        fs.writeFileSync(passFile, answer.trim(), { mode: 0o600 });
        log("  Keychain password saved. Background jobs will auto-unlock.");
      } else {
        log("  Skipped. Background jobs may fail with 'Not logged in' if Keychain locks.");
        log("  Run: echo 'YOUR_PASSWORD' > ~/.nexo/config/.keychain-pass && chmod 600 ~/.nexo/config/.keychain-pass");
      }
      resolve();
    });
  });
}

function isProtectedMacPath(candidate) {
  if (process.platform !== "darwin" || !candidate) return false;
  const homeDir = require("os").homedir();
  const expanded = candidate.replace(/^~/, homeDir);
  const resolved = path.resolve(expanded);
  const protectedRoots = [
    path.join(homeDir, "Documents"),
    path.join(homeDir, "Desktop"),
    path.join(homeDir, "Downloads"),
    path.join(homeDir, "Library", "Mobile Documents"),
  ];
  return protectedRoots.some((root) => resolved === root || resolved.startsWith(`${root}${path.sep}`));
}

function logMacPermissionsNotice(nexoHome, pythonPath = "") {
  if (!isProtectedMacPath(nexoHome)) return;
  log("macOS protected-folder warning:");
  log(`  NEXO_HOME is inside a protected folder: ${nexoHome}`);
  log("  Background jobs may fail with 'Operation not permitted'.");
  log("  Recommended: move NEXO_HOME outside Documents/Desktop/Downloads/iCloud Drive.");
  log("  If you keep it there, grant Full Disk Access to /bin/bash and your Python runtime.");
  if (pythonPath) {
    log(`  Python runtime: ${pythonPath}`);
  }
  log("  System Settings → Privacy & Security → Full Disk Access");
}

function getRuntimePythonTargets(pythonPath = "") {
  const candidates = [];
  const venvPy = path.join(NEXO_HOME, ".venv", "bin", "python3");
  if (fs.existsSync(venvPy)) candidates.push(venvPy);
  if (pythonPath) candidates.push(pythonPath);
  const discovered = run("which python3") || run("which python") || "";
  if (discovered) candidates.push(discovered);
  return [...new Set(candidates.filter(Boolean))];
}

function detectFullDiskAccessReasons(nexoHome) {
  if (process.platform !== "darwin") return [];
  const reasons = [];
  if (isProtectedMacPath(nexoHome)) {
    reasons.push(`NEXO_HOME is inside a protected macOS folder: ${nexoHome}`);
  }

  const logsDir = resolveRuntimeLogsDir(nexoHome);
  if (fs.existsSync(logsDir)) {
    const candidates = fs.readdirSync(logsDir).filter((name) => name.endsWith("-stderr.log"));
    for (const name of candidates) {
      try {
        const text = fs.readFileSync(path.join(logsDir, name), "utf8");
        if (text.includes("Operation not permitted")) {
          reasons.push(`Recent background job stderr hit 'Operation not permitted' (${name})`);
          break;
        }
      } catch {}
    }
  }
  return reasons;
}

function probeFullDiskAccess(nexoHome) {
  if (process.platform !== "darwin") {
    return { checked: false, granted: null, probePath: "", message: "macOS-only" };
  }

  const candidates = [
    path.join(require("os").homedir(), "Library", "Application Support", "com.apple.TCC", "TCC.db"),
    path.join(require("os").homedir(), "Library", "Mail"),
    path.join(require("os").homedir(), "Library", "Messages"),
    path.join(require("os").homedir(), "Library", "Safari"),
    path.join(require("os").homedir(), "Library", "Application Support", "AddressBook"),
  ].filter((item) => fs.existsSync(item));

  if (isProtectedMacPath(nexoHome)) candidates.push(nexoHome);
  if (!candidates.length) {
    return { checked: false, granted: null, probePath: "", message: "No probe path available." };
  }

  const seen = new Set();
  for (const candidate of candidates) {
    if (!candidate || seen.has(candidate)) continue;
    seen.add(candidate);
    const result = spawnSync("/bin/bash", [
      "-lc",
      'TARGET="$1"; if [ -d "$TARGET" ]; then ls "$TARGET" >/dev/null 2>&1; else head -c 1 "$TARGET" >/dev/null 2>&1; fi',
      "_",
      candidate,
    ], { encoding: "utf8" });
    if (result.status === 0) {
      return { checked: true, granted: true, probePath: candidate, message: "" };
    }
  }
  return { checked: true, granted: false, probePath: candidates[0], message: "Could not verify Full Disk Access yet." };
}

async function maybeConfigureFullDiskAccess(schedule, useDefaults, pythonPath = "") {
  const current = String((schedule && schedule.full_disk_access_status) || "unset").toLowerCase();
  schedule.full_disk_access_status_version = 1;
  const reasons = detectFullDiskAccessReasons(NEXO_HOME);
  schedule.full_disk_access_reasons = reasons;

  if (process.platform !== "darwin" || !reasons.length) {
    fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  if (current === "granted") {
    const probe = probeFullDiskAccess(NEXO_HOME);
    if (probe.granted) {
      fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
      return schedule;
    }
    schedule.full_disk_access_status = "later";
  } else if (current === "declined") {
    fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  if (useDefaults || !process.stdin.isTTY || !process.stdout.isTTY) {
    schedule.full_disk_access_status = current === "granted" ? "later" : current || "unset";
    fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  console.log("");
  log("Optional macOS Full Disk Access guidance:");
  log("macOS does not allow granting this automatically. NEXO can only open the correct System Settings screen and verify best effort.");
  log("Reason(s) detected:");
  reasons.forEach((item) => log(`  - ${item}`));
  log("If you proceed, add your terminal app and, if needed for background jobs, these binaries:");
  log("  - /bin/bash");
  getRuntimePythonTargets(pythonPath).forEach((item) => log(`  - ${item}`));

  const answer = (await ask("  Open Full Disk Access setup now? [y/N/later]: ")).trim().toLowerCase();
  if (answer === "y" || answer === "yes") {
    spawnSync("open", [MACOS_FDA_SETTINGS_URL], { stdio: "ignore" });
    log("Opened System Settings → Privacy & Security → Full Disk Access.");
    const followUp = (await ask("  Press Enter after granting it, or type later to skip for now: ")).trim().toLowerCase();
    if (followUp === "later" || followUp === "l") {
      schedule.full_disk_access_status = "later";
    } else {
      const probe = probeFullDiskAccess(NEXO_HOME);
      if (probe.granted) {
        schedule.full_disk_access_status = "granted";
        log(`Full Disk Access verified via ${probe.probePath}.`);
      } else {
        schedule.full_disk_access_status = "later";
        log("Could not verify Full Disk Access yet. NEXO will remind you later if background jobs still hit TCC.");
      }
    }
  } else if (answer === "later" || answer === "l" || answer === "") {
    schedule.full_disk_access_status = "later";
  } else {
    schedule.full_disk_access_status = "declined";
  }

  fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
  return schedule;
}

// ══════════════════════════════════════════════════════════════════════════════
// CORE PROCESS & HOOK DEFINITIONS
// All core nightly/periodic processes and all 8 core hooks that make NEXO functional.
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Complete definition of all core NEXO automated processes.
 * Each entry specifies the script, its interpreter ("python" or "bash"),
 * the schedule type, and default schedule values.
 */
const ALL_PROCESSES = [
  // --- Every 5 minutes ---
  { name: "auto-close-sessions", script: "auto_close_sessions.py", interpreter: "python", scriptDir: "root",
    type: "interval", intervalMinutes: 5, purpose: "Clean stale sessions" },
  // --- Every 30 minutes ---
  { name: "watchdog", script: "nexo-watchdog.sh", interpreter: "bash", scriptDir: "scripts",
    type: "interval", intervalMinutes: 30, purpose: "Health monitoring" },
  { name: "immune", script: "nexo-immune.py", interpreter: "python", scriptDir: "scripts",
    type: "interval", intervalMinutes: 30, purpose: "System immunity checks" },
  // --- Every 2 hours ---
  { name: "synthesis", script: "nexo-synthesis.py", interpreter: "python", scriptDir: "scripts",
    type: "interval", intervalMinutes: 120, optional: "automation", purpose: "Memory synthesis" },
  // --- Every hour ---
  { name: "backup", script: "nexo-backup.sh", interpreter: "bash", scriptDir: "scripts",
    type: "interval", intervalMinutes: 60, purpose: "DB backups" },
  // --- RunAtLoad (once on boot) ---
  { name: "catchup", script: "nexo-catchup.py", interpreter: "python", scriptDir: "scripts",
    type: "runAtLoad", purpose: "Session catchup" },
  { name: "tcc-approve", script: "nexo-tcc-approve.sh", interpreter: "bash", scriptDir: "scripts",
    type: "runAtLoad", macOnly: true, watchPaths: ["~/.local/share/claude/versions"],
    purpose: "Auto-approve macOS permissions for Claude updates" },
  // --- KeepAlive (persistent daemon) ---
  { name: "prevent-sleep", script: "nexo-prevent-sleep.sh", interpreter: "bash", scriptDir: "scripts",
    type: "keepAlive", purpose: "Keep machine awake for nocturnal processes" },
  { name: "dashboard", script: "nexo-dashboard.sh", interpreter: "bash", scriptDir: "scripts",
    type: "keepAlive", optional: "dashboard", purpose: "Web dashboard at localhost:6174" },
  // --- Daily (times from schedule.json) ---
  { name: "cognitive-decay", script: "nexo-cognitive-decay.py", interpreter: "python", scriptDir: "scripts",
    type: "daily", defaultHour: 3, defaultMinute: 0, purpose: "Memory decay" },
  { name: "postmortem", script: "nexo-postmortem-consolidator.py", interpreter: "python", scriptDir: "scripts",
    type: "daily", defaultHour: 23, defaultMinute: 30, optional: "automation", purpose: "Session consolidation" },
  { name: "self-audit", script: "nexo-daily-self-audit.py", interpreter: "python", scriptDir: "scripts",
    type: "daily", defaultHour: 7, defaultMinute: 0, optional: "automation", purpose: "Self-diagnostic" },
  { name: "sleep", script: "nexo-sleep.py", interpreter: "python", scriptDir: "scripts",
    type: "daily", defaultHour: 4, defaultMinute: 0, optional: "automation", purpose: "Sleep cycle" },
  { name: "deep-sleep", script: "nexo-deep-sleep.sh", interpreter: "bash", scriptDir: "scripts",
    type: "daily", defaultHour: 4, defaultMinute: 30, optional: "automation", purpose: "Deep sleep analysis" },
  // --- Weekly (day + time from schedule.json) ---
  { name: "evolution", script: "nexo-evolution-run.py", interpreter: "python", scriptDir: "scripts",
    type: "weekly", defaultDay: "sunday", defaultHour: 3, defaultMinute: 0, optional: "automation", purpose: "Self-evolution" },
  { name: "followup-hygiene", script: "nexo-followup-hygiene.py", interpreter: "python", scriptDir: "scripts",
    type: "weekly", defaultDay: "sunday", defaultHour: 5, defaultMinute: 0, purpose: "Cleanup stale followups" },
];

/**
 * Complete definition of all 8 core hooks.
 * event: Claude Code hook event name
 * matcher: glob matcher for the hook
 * script: script filename inside NEXO_HOME/hooks/ (or a raw command template)
 * key: unique identifier to detect if already registered (avoids duplicates)
 * timeout: seconds before Claude Code kills the hook (prevents hangs)
 */
// v6.0.0 — Core hook list is driven entirely by src/hooks/manifest.json.
// Each entry declares the Claude Code event and the relative path to the
// .py handler inside the installed runtime. Every handler receives a
// short alias key used to detect existing registrations in settings.hooks.
const HOOK_TIMEOUTS = {
  SessionStart:     40,
  Stop:             15,
  PreCompact:       15,
  PostCompact:      15,
  UserPromptSubmit:  5,
  PostToolUse:      20,
  Notification:      3,
  SubagentStop:     10,
};

function _manifestHookEntries() {
  return (_HOOKS_MANIFEST.hooks || []).map((entry) => {
    const handlerRel = String(entry.handler || "").trim();
    const handlerBase = handlerRel.split("/").pop() || handlerRel;
    return {
      event: entry.event,
      handler: handlerRel,
      key: handlerBase,
      critical: Boolean(entry.critical),
      timeout: HOOK_TIMEOUTS[entry.event] || 10,
    };
  }).filter((h) => h.event && h.handler);
}

function _hookCommand(hook, hooksDir, nexoHome) {
  // Resolve handler path under the installed runtime. hooksDir points to
  // the canonical installed hook tree (preferably ~/.nexo/core/hooks).
  const handlerFile = path.basename(hook.handler);
  const runtimePath = path.join(hooksDir, handlerFile);
  return `NEXO_HOME=${nexoHome} python3 ${runtimePath}`;
}

function _writeHooksStatus(nexoHome, manifestEntries, registrations) {
  // Publish the canonical hook-health contract under runtime/operations/.
  // Keep ~/.nexo/hooks_status.json only as a legacy alias so the root tree
  // does not remain the live source of truth.
  try {
    const now = new Date();
    const pkgJson = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
    const total = manifestEntries.length;
    const registered = registrations.filter((r) => r.status === "active").length;
    const healthy = total > 0 && registered === total;
    const payload = {
      generated_at: now.toISOString().replace(/\.\d+Z$/, "Z"),
      nexo_version: pkgJson.version || "unknown",
      total,
      registered,
      healthy,
      hooks: registrations,
    };
    const body = JSON.stringify(payload, null, 2) + "\n";
    const canonicalDir = path.join(nexoHome, "runtime", "operations");
    const canonicalPath = path.join(canonicalDir, "hooks_status.json");
    const legacyPath = path.join(nexoHome, "hooks_status.json");

    fs.mkdirSync(canonicalDir, { recursive: true });
    fs.writeFileSync(canonicalPath, body);

    try {
      if (fs.existsSync(legacyPath) || fs.lstatSync(legacyPath).isSymbolicLink()) {
        fs.rmSync(legacyPath, { force: true });
      }
    } catch (_) {}

    try {
      const relTarget = path.relative(path.dirname(legacyPath), canonicalPath) || path.basename(canonicalPath);
      fs.symlinkSync(relTarget, legacyPath);
    } catch (_) {
      fs.writeFileSync(legacyPath, body);
    }
  } catch (_) {}
}

/**
 * Register every hook declared by src/hooks/manifest.json into the
 * Claude Code settings file. Idempotent, never removes user-owned hooks.
 * Writes the canonical hook-status contract after each run so NEXO Desktop
 * can display hook health without parsing settings.json.
 */
function registerAllCoreHooks(settings, hooksDir, nexoHome) {
  if (!settings.hooks) settings.hooks = {};

  // Ensure operations dir exists for any hook that wants to drop a file there
  // (session-start.py writes .session-start-ts here).
  fs.mkdirSync(path.join(nexoHome, "runtime", "operations"), { recursive: true });

  const manifestEntries = _manifestHookEntries();
  const registrations = [];

  for (const hook of manifestEntries) {
    if (!settings.hooks[hook.event]) settings.hooks[hook.event] = [];

    const command = _hookCommand(hook, hooksDir, nexoHome);
    let status = "active";
    let found = false;

    for (let idx = 0; idx < settings.hooks[hook.event].length; idx++) {
      const entry = settings.hooks[hook.event][idx];
      if (entry.hooks && Array.isArray(entry.hooks)) {
        if (!entry.matcher) entry.matcher = "*";
        const subIdx = entry.hooks.findIndex(
          (h) => h.command && h.command.includes(hook.key),
        );
        if (subIdx !== -1) {
          const existing = entry.hooks[subIdx];
          if (existing.command !== command) existing.command = command;
          if (hook.timeout) existing.timeout = hook.timeout;
          found = true;
          break;
        }
      } else if (entry.command && entry.command.includes(hook.key)) {
        const migrated = { type: "command", command };
        if (hook.timeout) migrated.timeout = hook.timeout;
        settings.hooks[hook.event][idx] = {
          matcher: "*",
          hooks: [migrated],
        };
        found = true;
        break;
      }
    }

    if (!found) {
      const newHook = { type: "command", command };
      if (hook.timeout) newHook.timeout = hook.timeout;
      settings.hooks[hook.event].push({
        matcher: "*",
        hooks: [newHook],
      });
    }

    // Confirm the handler file exists on disk; if not, mark error.
    const handlerAbs = path.join(hooksDir, path.basename(hook.handler));
    if (!fs.existsSync(handlerAbs)) {
      status = "error";
    }

    registrations.push({
      event: hook.event,
      handler: path.basename(hook.handler),
      status,
    });
  }

  _writeHooksStatus(nexoHome, manifestEntries, registrations);

  // v6.0.0 — also purge any stale v5.x hook commands that referenced the
  // old .sh scripts directly (post-compact.sh, heartbeat-user-msg.sh,
  // protocol-guardrail.sh, etc.) so a pre-existing install migrates
  // cleanly to the manifest-driven world. Only removes NEXO-owned
  // entries, leaves user-custom hooks alone.
  const LEGACY_KEYS = [
    "daily-briefing-check.sh",
    "capture-tool-logs.sh",
    "capture-session.sh",
    "inbox-hook.sh",
    "heartbeat-posttool.sh",
    "heartbeat-user-msg.sh",
    "protocol-guardrail.sh",
    "protocol-pretool-guardrail.sh",
    "post-compact.sh",
    ".session-start-ts",
  ];
  for (const event of Object.keys(settings.hooks)) {
    const entries = settings.hooks[event];
    if (!Array.isArray(entries)) continue;
    const manifestEventHandlers = new Set(
      manifestEntries.filter((h) => h.event === event).map((h) => h.key),
    );
    for (let i = entries.length - 1; i >= 0; i--) {
      const entry = entries[i];
      if (entry && entry.hooks && Array.isArray(entry.hooks)) {
        entry.hooks = entry.hooks.filter((h) => {
          const cmd = String(h.command || "");
          // Keep anything the manifest owns.
          if (Array.from(manifestEventHandlers).some((k) => cmd.includes(k))) {
            return true;
          }
          // Drop strictly-legacy NEXO-owned commands.
          if (LEGACY_KEYS.some((legacy) => cmd.includes(legacy))) {
            return false;
          }
          return true;
        });
        if (entry.hooks.length === 0) {
          entries.splice(i, 1);
        }
      }
    }
    if (entries.length === 0) delete settings.hooks[event];
  }
}

/**
 * Load schedule.json if it exists, or create it with defaults on fresh install.
 * NEVER overwrites an existing schedule.json (user customization).
 */
function loadOrCreateSchedule(nexoHome) {
  const configDir = resolveRuntimeConfigDir(nexoHome);
  fs.mkdirSync(configDir, { recursive: true });
  const scheduleFile = path.join(configDir, "schedule.json");

  if (fs.existsSync(scheduleFile)) {
    try {
      return JSON.parse(fs.readFileSync(scheduleFile, "utf8"));
    } catch {
      // Corrupt file — return defaults but don't overwrite
      return getDefaultSchedule();
    }
  }

  // Fresh install: detect timezone and create schedule.json
  const detectedTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const schedule = getDefaultSchedule(detectedTz);
  fs.writeFileSync(scheduleFile, JSON.stringify(schedule, null, 2));
  return schedule;
}

function getDefaultSchedule(timezone) {
  return {
    timezone: timezone || "UTC",
    auto_update: true,
    interactive_clients: {
      claude_code: true,
      codex: false,
      claude_desktop: false,
    },
    default_terminal_client: "claude_code",
    automation_enabled: true,
    automation_backend: "claude_code",
    // v6.0.0 — model/reasoning_effort have moved to src/resonance_tiers.json
    // keyed by the operator's preferences.default_resonance. The shape
    // below stays so that downstream readers that iterate the profile
    // dict do not need a guard, but the concrete values no longer live
    // in schedule.json.
    client_runtime_profiles: {
      claude_code: {},
      codex: {},
    },
    client_install_preferences: {
      claude_code: "ask",
      codex: "ask",
      claude_desktop: "manual",
    },
    power_policy: "unset",
    power_policy_version: 2,
    full_disk_access_status: "unset",
    full_disk_access_status_version: 1,
    full_disk_access_reasons: [],
    public_contribution: {
      enabled: false,
      mode: "unset",
      consent_version: 1,
      github_user: "",
      upstream_repo: PUBLIC_CONTRIBUTION_UPSTREAM,
      fork_repo: "",
      machine_id: crypto.createHash("sha1").update(require("os").hostname()).digest("hex").slice(0, 12),
      active_pr_url: "",
      active_pr_number: null,
      active_branch: "",
      status: "unset",
      cooldown_until: "",
      last_run_at: "",
      last_result: "",
    },
    processes: {
      "cognitive-decay": { hour: 3, minute: 0 },
      "postmortem": { hour: 23, minute: 30 },
      "self-audit": { hour: 7, minute: 0 },
      "sleep": { hour: 4, minute: 0 },
      "deep-sleep": { hour: 4, minute: 30 },
      "evolution": { day: "sunday", hour: 3, minute: 0 },
      "followup-hygiene": { day: "sunday", hour: 5, minute: 0 },
    },
  };
}

function writeDesktopProductMode(nexoHome) {
  if (!isDesktopManagedInstall()) return;
  const configDir = resolveRuntimeConfigDir(nexoHome);
  fs.mkdirSync(configDir, { recursive: true });
  const target = path.join(configDir, "product-mode.json");
  let createdAt = new Date().toISOString();
  if (fs.existsSync(target)) {
    try {
      const existing = JSON.parse(fs.readFileSync(target, "utf8"));
      if (existing && typeof existing === "object" && existing.created_at) {
        createdAt = existing.created_at;
      }
    } catch (_) {}
  }
  fs.writeFileSync(target, JSON.stringify({
    desktop_managed: true,
    product_mode: "desktop_closed_product",
    disabled_features: ["evolution"],
    source: "desktop",
    created_at: createdAt,
    updated_at: new Date().toISOString(),
  }, null, 2));
}

function ensureEvolutionObjectiveForCurrentProductMode(nexoHome) {
  const brainDir = resolveRuntimeBrainDir(nexoHome);
  fs.mkdirSync(brainDir, { recursive: true });
  const evoObjectivePath = path.join(brainDir, "evolution-objective.json");
  const desktopManaged = isDesktopManagedInstall();
  let payload = null;
  if (fs.existsSync(evoObjectivePath)) {
    try {
      payload = JSON.parse(fs.readFileSync(evoObjectivePath, "utf8"));
    } catch (_) {
      payload = null;
    }
  }
  if (!payload || typeof payload !== "object") {
    payload = {
      objective: "Improve operational excellence and reduce repeated errors",
      focus_areas: ["error_prevention", "proactivity", "memory_quality"],
      evolution_enabled: true,
      evolution_mode: "auto",
      dimensions: {
        episodic_memory: { current: 0, target: 90 },
        autonomy: { current: 0, target: 80 },
        proactivity: { current: 0, target: 70 },
        self_improvement: { current: 0, target: 60 },
        agi: { current: 0, target: 20 },
      },
      total_evolutions: 0,
      consecutive_failures: 0,
      created_at: new Date().toISOString(),
    };
  }
  if (desktopManaged) {
    payload.evolution_enabled = false;
    payload.disabled_reason = "Disabled by NEXO Desktop product contract";
    payload.disabled_by = "desktop_product";
    payload.desktop_managed = true;
  }
  fs.writeFileSync(evoObjectivePath, JSON.stringify(payload, null, 2));
  return evoObjectivePath;
}

function normalizePublicContributionConfig(config = {}) {
  const base = getDefaultSchedule().public_contribution;
  const merged = { ...base, ...(config || {}) };
  merged.enabled = Boolean(merged.enabled);
  merged.mode = String(merged.mode || "unset").toLowerCase();
  merged.status = String(merged.status || "unset").toLowerCase();
  merged.github_user = String(merged.github_user || "").trim();
  merged.fork_repo = String(merged.fork_repo || "").trim();
  merged.upstream_repo = String(merged.upstream_repo || PUBLIC_CONTRIBUTION_UPSTREAM).trim() || PUBLIC_CONTRIBUTION_UPSTREAM;
  merged.active_pr_url = String(merged.active_pr_url || "").trim();
  merged.active_branch = String(merged.active_branch || "").trim();
  merged.cooldown_until = String(merged.cooldown_until || "").trim();
  merged.last_run_at = String(merged.last_run_at || "").trim();
  merged.last_result = String(merged.last_result || "").trim();
  return merged;
}

function detectInstalledClients() {
  const homeDir = require("os").homedir();
  const desktopConfig = process.platform === "darwin"
    ? path.join(homeDir, "Library", "Application Support", "Claude", "claude_desktop_config.json")
    : process.platform === "win32"
      ? path.join(homeDir, "AppData", "Roaming", "Claude", "claude_desktop_config.json")
      : path.join(homeDir, ".config", "Claude", "claude_desktop_config.json");
  const desktopApps = process.platform === "darwin"
    ? [path.join(homeDir, "Applications", "Claude.app"), "/Applications/Claude.app"]
    : [];
  const desktopAppPath = desktopApps.find((candidate) => fs.existsSync(candidate)) || "";
  const managedClaudeBin = resolveManagedClaudeBinary();
  const persistedClaudeBin = readPersistedClaudeCliPath();
  const claudeBin = managedClaudeBin || persistedClaudeBin || run("which claude", { env: buildManagedCliEnv() }) || run("which claude") || "";
  const codexBin = run("which codex") || "";
  return {
    claude_code: {
      installed: Boolean(claudeBin),
      path: claudeBin,
      detectedBy: claudeBin ? "binary" : "missing",
    },
    codex: {
      installed: Boolean(codexBin),
      path: codexBin,
      detectedBy: codexBin ? "binary" : "missing",
    },
    claude_desktop: {
      installed: Boolean(desktopAppPath || fs.existsSync(desktopConfig)),
      path: desktopAppPath || desktopConfig,
      detectedBy: desktopAppPath ? "app" : (fs.existsSync(desktopConfig) ? "config" : "missing"),
    },
  };
}

function managedClaudePrefix() {
  const explicit = String(process.env.NEXO_CLAUDE_PREFIX || "").trim();
  if (explicit) return explicit;
  return path.join(NEXO_HOME, "runtime", "bootstrap", "npm-global");
}

function buildManagedCliEnv(extraEnv = {}) {
  const prefix = managedClaudePrefix();
  const parts = [
    path.join(prefix, "bin"),
    process.env.PATH || "",
  ].filter(Boolean);
  return {
    ...process.env,
    npm_config_prefix: prefix,
    PATH: parts.join(path.delimiter),
    ...extraEnv,
  };
}

function ensureDesktopNodeShim(desktopNode) {
  const clean = String(desktopNode || "").trim();
  if (!clean) return "";
  const shimDir = path.join(NEXO_HOME, "runtime", "bootstrap", "node-shim");
  fs.mkdirSync(shimDir, { recursive: true });
  if (process.platform === "win32") {
    const shimPath = path.join(shimDir, "node.cmd");
    fs.writeFileSync(
      shimPath,
      `@echo off\r\nset ELECTRON_RUN_AS_NODE=1\r\n"${clean}" %*\r\n`,
    );
    return shimDir;
  }
  const shimPath = path.join(shimDir, "node");
  fs.writeFileSync(
    shimPath,
    [
      "#!/bin/sh",
      "export ELECTRON_RUN_AS_NODE=1",
      `exec ${shSingleQuote(clean)} "$@"`,
      "",
    ].join("\n"),
  );
  fs.chmodSync(shimPath, 0o755);
  return shimDir;
}

function withDesktopNodeShim(env, desktopNode) {
  try {
    const shimDir = ensureDesktopNodeShim(desktopNode);
    if (!shimDir) return env;
    return {
      ...env,
      ELECTRON_RUN_AS_NODE: "1",
      PATH: [shimDir, env.PATH || ""].filter(Boolean).join(path.delimiter),
    };
  } catch (err) {
    log(`Desktop Node shim could not be created: ${String(err && err.message || err)}`);
    return env;
  }
}

function resolveManagedClaudeBinary() {
  const prefix = managedClaudePrefix();
  const candidates = process.platform === "win32"
    ? [path.join(prefix, "claude.cmd"), path.join(prefix, "bin", "claude.cmd")]
    : [path.join(prefix, "bin", "claude"), path.join(prefix, "claude")];
  return candidates.find((candidate) => candidate && fs.existsSync(candidate)) || "";
}

function readPersistedClaudeCliPath() {
  const candidates = [
    path.join(NEXO_HOME, "config", "claude-cli-path"),
    path.join(NEXO_HOME, "personal", "config", "claude-cli-path"),
  ];
  for (const file of candidates) {
    try {
      if (!fs.existsSync(file)) continue;
      const value = String(fs.readFileSync(file, "utf8") || "").trim();
      if (value && fs.existsSync(value)) return value;
    } catch {}
  }
  return "";
}

function persistClaudeCliPath(claudePath) {
  const value = String(claudePath || "").trim();
  if (!value) return;
  const targets = [
    path.join(NEXO_HOME, "config", "claude-cli-path"),
    path.join(NEXO_HOME, "personal", "config", "claude-cli-path"),
  ];
  for (const file of targets) {
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, value);
    } catch {}
  }
}

function clientSetupStrings(lang) {
  if (lang === "es") {
    return {
      title: "Shared brain siempre activo. Ahora elige clientes y backend de automatización.",
      detected: "Clientes detectados",
      yes: "sí",
      no: "no",
      useClaudeCodeQ: "  ¿Quieres usar Claude Code como cliente interactivo? (recomendado)",
      useCodexQ: "  ¿Quieres usar Codex como cliente interactivo?",
      useDesktopQ: "  ¿Quieres conectar Claude Desktop al mismo brain?",
      defaultTerminalQ: "  ¿Qué cliente debe abrir `nexo chat` por defecto?",
      automationQ: "  ¿Quieres automatización en background? (sleep, deep-sleep, synthesis, self-audit, evolution, postmortem)",
      automationBackendQ: "  ¿Qué backend debe ejecutar esa automatización?",
      installClaudeQ: "  Claude Code no está instalado. ¿Quieres instalarlo ahora?",
      installCodexQ: "  Codex no está instalado. ¿Quieres instalarlo ahora?",
      installingClaude: "Instalando Claude Code...",
      installingCodex: "Instalando Codex...",
      desktopManual: "Claude Desktop no se instala desde NEXO. Cuando exista, se conectará con la sync de clientes.",
      terminalFallback: (label) => `El cliente terminal elegido no está disponible. \`nexo chat\` quedará pendiente hasta instalar ${label}.`,
      automationDisabled: (label) => `El backend ${label} sigue sin estar disponible. Se desactiva la automatización por ahora.`,
      summary: (defaultClient, defaultProfile, backend, backendProfile, automationEnabled) =>
        `Configuración clientes: chat=${defaultClient}(${defaultProfile}), automation=${automationEnabled ? `${backend}(${backendProfile})` : "none"}`,
    };
  }
  return {
    title: "Shared brain is always on. Now choose your clients and automation backend.",
    detected: "Detected clients",
    yes: "yes",
    no: "no",
    useClaudeCodeQ: "  Use Claude Code as an interactive client? (recommended)",
    useCodexQ: "  Use Codex as an interactive client?",
    useDesktopQ: "  Connect Claude Desktop to the same brain?",
    defaultTerminalQ: "  Which client should `nexo chat` open by default?",
    automationQ: "  Enable background automation? (sleep, deep-sleep, synthesis, self-audit, evolution, postmortem)",
    automationBackendQ: "  Which backend should run that automation?",
    installClaudeQ: "  Claude Code is not installed. Install it now?",
    installCodexQ: "  Codex is not installed. Install it now?",
    installingClaude: "Installing Claude Code...",
    installingCodex: "Installing Codex...",
    desktopManual: "Claude Desktop is not installed by NEXO. When it appears, client sync will connect it.",
    terminalFallback: (label) => `The selected terminal client is still unavailable. \`nexo chat\` will stay pending until ${label} is installed.`,
    automationDisabled: (label) => `${label} is still unavailable. Disabling background automation for now.`,
    summary: (defaultClient, defaultProfile, backend, backendProfile, automationEnabled) =>
      `Client setup: chat=${defaultClient}(${defaultProfile}), automation=${automationEnabled ? `${backend}(${backendProfile})` : "none"}`,
  };
}

function _yn(answer, defaultValue) {
  const value = String(answer || "").trim().toLowerCase();
  if (!value) return defaultValue;
  if (["y", "yes", "s", "si", "sí", "1"].includes(value)) return true;
  if (["n", "no", "0"].includes(value)) return false;
  return defaultValue;
}

async function askYesNo(question, defaultValue) {
  const suffix = defaultValue ? " [Y/n]: " : " [y/N]: ";
  const answer = await ask(question + suffix);
  return _yn(answer, defaultValue);
}

async function askChoice(question, options, defaultValue) {
  let prompt = `${question}\n`;
  options.forEach((option, idx) => {
    const marker = option.value === defaultValue ? " (default)" : "";
    prompt += `    ${idx + 1}. ${option.label}${marker}\n`;
  });
  prompt += "  > ";
  const answer = (await ask(prompt)).trim().toLowerCase();
  if (!answer) return defaultValue;
  const asIndex = parseInt(answer, 10);
  if (!Number.isNaN(asIndex) && asIndex >= 1 && asIndex <= options.length) {
    return options[asIndex - 1].value;
  }
  const byValue = options.find((option) => option.value === answer);
  return byValue ? byValue.value : defaultValue;
}

function defaultClientRuntimeProfiles() {
  // v6.0.0 — no more model/reasoning_effort here. The resonance tier
  // (preferences.default_resonance in calibration.json) plus
  // src/resonance_tiers.json drive the actual model and effort at runtime.
  return {
    claude_code: {},
    codex: {},
  };
}

function runtimeClientLabel(client) {
  if (client === "claude_code") return "Claude Code";
  if (client === "codex") return "Codex";
  return client;
}

function formatRuntimeProfile(profile = {}) {
  const model = String(profile.model || "").trim();
  const effort = String(profile.reasoning_effort || "").trim();
  return effort ? `${model}/${effort}` : model;
}

// v6.0.0 — Tier-only setup. Onboarding asks the operator for one resonance
// tier (maximo / alto / medio / bajo) and that choice drives every backend
// via src/resonance_tiers.json. No more model or effort questions.
async function askResonanceTier(lang, currentTier) {
  const recommended = lang === "es" ? " (recomendado)" : " (recommended)";
  const question = lang === "es"
    ? "  ¿Qué nivel de potencia quieres por defecto para tus conversaciones?"
    : "  Which default power level do you want for your conversations?";
  const options = [
    { value: "maximo", label: lang === "es" ? "máximo"                 : "maximum" },
    { value: "alto",   label: (lang === "es" ? "alto"                  : "high") + recommended },
    { value: "medio",  label: lang === "es" ? "medio"                  : "medium" },
    { value: "bajo",   label: lang === "es" ? "bajo"                   : "low" },
  ];
  const fallback = RESONANCE_TIER_NAMES.includes(currentTier) ? currentTier : DEFAULT_RESONANCE_TIER;
  const chosen = await askChoice(question, options, fallback);
  return RESONANCE_TIER_NAMES.includes(chosen) ? chosen : DEFAULT_RESONANCE_TIER;
}

function defaultClientSetup(detected) {
  return {
    interactive_clients: {
      claude_code: true,
      codex: Boolean(detected.codex.installed),
      claude_desktop: Boolean(detected.claude_desktop.installed),
    },
    default_terminal_client: "claude_code",
    automation_enabled: true,
    automation_backend: "claude_code",
    client_runtime_profiles: defaultClientRuntimeProfiles(),
    client_install_preferences: {
      claude_code: "ask",
      codex: "ask",
      claude_desktop: "manual",
    },
  };
}

function applyClientSetupToSchedule(schedule, setup) {
  schedule.interactive_clients = {
    claude_code: Boolean(setup.interactive_clients.claude_code),
    codex: Boolean(setup.interactive_clients.codex),
    claude_desktop: Boolean(setup.interactive_clients.claude_desktop),
  };
  schedule.default_terminal_client = setup.default_terminal_client;
  schedule.automation_enabled = Boolean(setup.automation_enabled);
  schedule.automation_backend = schedule.automation_enabled ? setup.automation_backend : "none";
  schedule.client_runtime_profiles = {
    ...defaultClientRuntimeProfiles(),
    ...(setup.client_runtime_profiles || {}),
  };
  schedule.client_install_preferences = { ...(setup.client_install_preferences || {}) };
  return schedule;
}

function requiredCliClients(setup) {
  const required = new Set();
  if (setup.interactive_clients.claude_code || setup.default_terminal_client === "claude_code" || setup.automation_backend === "claude_code") {
    required.add("claude_code");
  }
  if (setup.interactive_clients.codex || setup.default_terminal_client === "codex" || setup.automation_backend === "codex") {
    required.add("codex");
  }
  return Array.from(required);
}

function installClaudeCodeCli(platform) {
  let claudeInstalled = detectInstalledClients().claude_code.path || "";
  if (claudeInstalled) {
    persistClaudeCliPath(claudeInstalled);
    return { installed: true, path: claudeInstalled };
  }

  const desktopNode = String(process.env.NEXO_DESKTOP_NODE || "").trim();
  const bundledNpmCli = String(process.env.NEXO_DESKTOP_NPM_CLI || "").trim();
  const managedPrefix = managedClaudePrefix();
  const desktopManaged = isDesktopManagedInstall();
  const npmViaDesktop = desktopNode && bundledNpmCli;
  let installEnv = buildManagedCliEnv();
  if (desktopNode) installEnv = withDesktopNodeShim(installEnv, desktopNode);

  // OFFLINE-FIRST v0.32.4: install claude-code wrapper + ALL its native packs
  // from bundled tarballs. Path: resources/brain-bundle/claude-code/*.tgz.
  //
  // Bug que arregla este fix (encontrado 2026-05-02):
  // claude-code 2.1.x ships como wrapper + 4 native packs por arquitectura
  // (@anthropic-ai/claude-code-linux-x64, -darwin-arm64, -darwin-x64,
  // -linux-arm64). Antes solo bundeaba el wrapper (.tgz 13 KB) y pasaba SOLO
  // ese al npm install. npm intentaba resolver los `optionalDependencies` del
  // registry online → fallo offline → wrapper instala SIN binario claude →
  // `command -v claude` exit 127 → bootstrap "claude-runtime-missing-soft".
  // Ahora bundeamos los 5 .tgz (wrapper + 4 native) y se los pasamos TODOS
  // a npm en un solo install. npm los ve como dependencias pre-resueltas
  // y skip el registry lookup. claude binary ejecutable resultante.
  const bundledClaudeDir = path.join(__dirname, "..", "claude-code");
  if (fs.existsSync(bundledClaudeDir)) {
    const allTgz = fs.readdirSync(bundledClaudeDir).filter((f) => f.endsWith(".tgz"));
    // Ordenar: el wrapper primero (sin sufijo de plataforma), después los packs.
    const wrapper = allTgz.find((f) => /^anthropic-ai-claude-code-\d/.test(f));
    // v7.12.6 — Filter native packs to ONLY the current platform/arch.
    // Bug: passing all 4 native packs to `npm install` triggers EBADPLATFORM
    // when npm refuses to install e.g. `claude-code-darwin-arm64` on linux/x64
    // (`{"os":"darwin","cpu":"arm64"}` vs current `{"os":"linux","cpu":"x64"}`).
    // The whole install aborts → `command -v claude` fails → bootstrap stalls
    // at "Preparando NEXO..." and never reaches Brain config. First seen
    // 2026-05-03 on Inma's Win11 PC running the Linux x64 WSL distro.
    const platformSlug = `${process.platform}-${process.arch}`;
    const nativePacks = allTgz.filter((f) => {
      if (f === wrapper) return false;
      // Native pack filenames look like `anthropic-ai-claude-code-<os>-<cpu>-<version>.tgz`.
      // Match the `-<os>-<cpu>-` segment against the runtime platform slug.
      return f.includes(`-${platformSlug}-`);
    });
    if (wrapper && nativePacks.length > 0) {
      const tgzPaths = [path.join(bundledClaudeDir, wrapper), ...nativePacks.map((p) => path.join(bundledClaudeDir, p))];
      log("  Installing claude-code from bundled tarballs (offline, " + (1 + nativePacks.length) + " packs)...");
      spawnSync(
        npmViaDesktop ? desktopNode : "npm",
        [
          ...(npmViaDesktop ? [bundledNpmCli] : []),
          "install",
          "-g",
          "--prefix",
          managedPrefix,
          "--offline",
          "--no-audit",
          "--no-fund",
          ...tgzPaths,
        ],
        { stdio: "inherit", env: installEnv },
      );
      claudeInstalled = detectInstalledClients().claude_code.path || "";
      if (claudeInstalled) {
        persistClaudeCliPath(claudeInstalled);
        return { installed: true, path: claudeInstalled };
      }
    } else if (wrapper) {
      // Fallback: solo wrapper (legacy bundle 0.32.3 y anteriores).
      const tgzPath = path.join(bundledClaudeDir, wrapper);
      log("  Installing claude-code from bundled wrapper only (legacy bundle, may need network for native pack)...");
      spawnSync(
        npmViaDesktop ? desktopNode : "npm",
        [
          ...(npmViaDesktop ? [bundledNpmCli] : []),
          "install",
          "-g",
          "--prefix",
          managedPrefix,
          tgzPath,
        ],
        { stdio: "inherit", env: installEnv },
      );
      claudeInstalled = detectInstalledClients().claude_code.path || "";
      if (claudeInstalled) {
        persistClaudeCliPath(claudeInstalled);
        return { installed: true, path: claudeInstalled };
      }
    }
  }

  if (desktopNode && bundledNpmCli) {
    spawnSync(
      desktopNode,
      [bundledNpmCli, "install", "-g", "--prefix", managedPrefix, "@anthropic-ai/claude-code"],
      {
        stdio: "inherit",
        env: { ...installEnv, ELECTRON_RUN_AS_NODE: "1" },
      },
    );
    claudeInstalled = detectInstalledClients().claude_code.path || "";
    if (claudeInstalled) {
      persistClaudeCliPath(claudeInstalled);
      return { installed: true, path: claudeInstalled };
    }
  }

  if (desktopManaged) {
    spawnSync(
      "npm",
      ["install", "-g", "--prefix", managedPrefix, "@anthropic-ai/claude-code"],
      {
        stdio: "inherit",
        env: installEnv,
      },
    );
    claudeInstalled = detectInstalledClients().claude_code.path || "";
    if (claudeInstalled) {
      persistClaudeCliPath(claudeInstalled);
      return { installed: true, path: claudeInstalled };
    }
    return { installed: false, path: "" };
  }

  spawnSync("npx", ["-y", "@anthropic-ai/claude-code", "--version"], {
    stdio: "pipe",
    timeout: 60000,
    env: installEnv,
  });
  claudeInstalled = detectInstalledClients().claude_code.path || "";
  if (!claudeInstalled) {
    const npmCmd = platform === "linux" ? "sudo" : "npm";
    const npmArgs = platform === "linux"
      ? ["npm", "install", "-g", "@anthropic-ai/claude-code"]
      : ["install", "-g", "--prefix", managedPrefix, "@anthropic-ai/claude-code"];
    spawnSync(npmCmd, npmArgs, { stdio: "inherit", env: installEnv });
    claudeInstalled = detectInstalledClients().claude_code.path || "";
  }
  if (claudeInstalled) persistClaudeCliPath(claudeInstalled);
  return { installed: Boolean(claudeInstalled), path: claudeInstalled || "" };
}

function installCodexCli() {
  const before = run("which codex");
  if (before) return { installed: true, path: before };
  spawnSync("npm", ["install", "-g", "@openai/codex"], { stdio: "inherit" });
  const codexInstalled = run("which codex") || "";
  return { installed: Boolean(codexInstalled), path: codexInstalled };
}

async function configureClientSetup({ lang, useDefaults, autoInstall, detected }) {
  const strings = clientSetupStrings(lang);
  const setup = defaultClientSetup(detected);
  const desktopManaged = String(process.env.NEXO_DESKTOP_MANAGED || "").trim() === "1";
  setup.client_install_preferences = {
    claude_code: autoInstall === "auto" ? "auto" : "ask",
    codex: autoInstall === "auto" ? "auto" : "ask",
    claude_desktop: "manual",
  };

  if (!useDefaults) {
    console.log("");
    log(strings.title);
    log(`${strings.detected}: Claude Code=${detected.claude_code.installed ? strings.yes : strings.no}, Codex=${detected.codex.installed ? strings.yes : strings.no}, Claude Desktop=${detected.claude_desktop.installed ? strings.yes : strings.no}`);
    setup.interactive_clients.claude_code = await askYesNo(strings.useClaudeCodeQ, detected.claude_code.installed || true);
    setup.interactive_clients.codex = await askYesNo(strings.useCodexQ, detected.codex.installed);
    setup.interactive_clients.claude_desktop = await askYesNo(strings.useDesktopQ, detected.claude_desktop.installed);

    const defaultTerminalChoices = [
      { value: "claude_code", label: lang === "es" ? "Claude Code (recomendado)" : "Claude Code (recommended)" },
      { value: "codex", label: "Codex" },
    ].filter((item) => setup.interactive_clients[item.value]);

    if (defaultTerminalChoices.length === 1) {
      setup.default_terminal_client = defaultTerminalChoices[0].value;
    } else if (defaultTerminalChoices.length > 1) {
      setup.default_terminal_client = await askChoice(
        strings.defaultTerminalQ,
        defaultTerminalChoices,
        setup.interactive_clients.codex && !setup.interactive_clients.claude_code ? "codex" : "claude_code",
      );
    }

    setup.automation_enabled = await askYesNo(strings.automationQ, true);
    if (setup.automation_enabled) {
      const backendDefault = setup.interactive_clients.codex && !setup.interactive_clients.claude_code ? "codex" : "claude_code";
      setup.automation_backend = await askChoice(
        strings.automationBackendQ,
        [
          { value: "claude_code", label: lang === "es" ? "Claude Code (recomendado)" : "Claude Code (recommended)" },
          { value: "codex", label: "Codex" },
        ],
        backendDefault,
      );
    } else {
      setup.automation_backend = "none";
    }
    console.log("");
  } else if (detected.codex.installed) {
    setup.interactive_clients.codex = true;
  }

  const required = requiredCliClients(setup);
  for (const client of required) {
    if (detected[client] && detected[client].installed) continue;
    if (desktopManaged && client === "claude_code") {
      const bundledClaudeDir = path.join(__dirname, "..", "claude-code");
      let hasBundle = false;
      try {
        if (fs.existsSync(bundledClaudeDir)) {
          hasBundle = fs.readdirSync(bundledClaudeDir).some((f) => f.endsWith(".tgz"));
        }
      } catch (_) {}
      if (!hasBundle) {
        log("Claude Code install deferred to Desktop final sync.");
        continue;
      }
      log("Bundled Claude Code tarball detected — installing offline now.");
    }
    let shouldInstall = useDefaults || autoInstall === "auto";
    if (!shouldInstall && process.stdin.isTTY && process.stdout.isTTY) {
      const question = client === "claude_code" ? strings.installClaudeQ : strings.installCodexQ;
      shouldInstall = await askYesNo(question, true);
    }
    if (!shouldInstall) continue;
    log(client === "claude_code" ? strings.installingClaude : strings.installingCodex);
    const outcome = client === "claude_code" ? installClaudeCodeCli(process.platform) : installCodexCli();
    detected = detectInstalledClients();
    if (outcome.installed && client === "claude_code") {
      log("Claude Code installed successfully.");
    } else if (outcome.installed && client === "codex") {
      log("Codex installed successfully.");
    }
  }

  if (setup.default_terminal_client && !detected[setup.default_terminal_client]?.installed) {
    const fallback = ["claude_code", "codex"].find((key) => key !== setup.default_terminal_client && detected[key]?.installed && setup.interactive_clients[key]);
    if (fallback) {
      setup.default_terminal_client = fallback;
      log(`Default terminal client fallback: ${fallback}`);
    } else {
      const label = setup.default_terminal_client === "claude_code" ? "Claude Code" : "Codex";
      log(strings.terminalFallback(label));
    }
  }

  if (setup.automation_enabled && setup.automation_backend !== "none" && !detected[setup.automation_backend]?.installed) {
    if (desktopManaged && setup.automation_backend === "claude_code") {
      log("Claude Code will be provisioned by Desktop after the core runtime is ready.");
      return { setup, detected };
    }
    const label = setup.automation_backend === "claude_code" ? "Claude Code" : "Codex";
    log(strings.automationDisabled(label));
    setup.automation_enabled = false;
    setup.automation_backend = "none";
  }

  if (!detected.claude_desktop.installed && setup.interactive_clients.claude_desktop) {
    log(strings.desktopManual);
  }

  // v6.0.0 — no per-client model/effort prompts. A single tier question
  // (asked by the main installer flow, not here) will write
  // preferences.default_resonance into calibration.json. All runtime
  // resolution then flows through src/resonance_tiers.json.

  const defaultProfile = "tier";
  const backendProfile = setup.automation_enabled && setup.automation_backend !== "none" ? "tier" : "";
  log(strings.summary(setup.default_terminal_client, defaultProfile, setup.automation_backend, backendProfile, setup.automation_enabled));
  return { setup, detected };
}

async function maybeConfigurePowerPolicy(schedule, useDefaults) {
  const current = String((schedule && schedule.power_policy) || "unset").toLowerCase();
  if (current && current !== "unset") {
    return schedule;
  }
  if (useDefaults || !process.stdin.isTTY || !process.stdout.isTTY) {
    schedule.power_policy = "unset";
    schedule.power_policy_version = 2;
    return schedule;
  }

  console.log("");
  log("Optional power policy:");
  log("If enabled, NEXO will activate a platform power helper for background work.");
  if (process.platform === "darwin") {
    log("On macOS this uses the native caffeinate helper. Closed-lid operation depends on your setup, so wake recovery remains active.");
  } else if (process.platform === "linux") {
    log("On Linux this uses systemd-inhibit or caffeine when available. Closed-lid behavior depends on host power settings.");
  }
  const answer = (await ask("  Enable the background power helper for this machine? [y/N/later]: ")).trim().toLowerCase();
  if (answer === "y" || answer === "yes") {
    schedule.power_policy = "always_on";
  } else if (answer === "later" || answer === "l") {
    schedule.power_policy = "unset";
  } else {
    schedule.power_policy = "disabled";
  }
  schedule.power_policy_version = 2;
  fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
  return schedule;
}

function ghLogin() {
  const login = run("gh api user --jq .login 2>/dev/null");
  return (login || "").trim();
}

function ensureFork(login) {
  if (!login) return { ok: false, message: "Missing GitHub login.", forkRepo: "" };
  const forkRepo = `${login}/nexo`;
  const existing = run(`gh repo view "${forkRepo}" --json nameWithOwner 2>/dev/null`);
  if (existing) return { ok: true, message: "", forkRepo };
  const created = run(`gh repo fork "${PUBLIC_CONTRIBUTION_UPSTREAM}" --clone=false --remote=false 2>/dev/null`);
  if (created !== null) return { ok: true, message: "", forkRepo };
  return { ok: false, message: `Could not ensure fork ${forkRepo}.`, forkRepo: "" };
}

async function maybeConfigurePublicContribution(schedule, useDefaults) {
  const current = normalizePublicContributionConfig((schedule && schedule.public_contribution) || {});
  if (current.mode && current.mode !== "unset") {
    schedule.public_contribution = current;
    fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  if (useDefaults || !process.stdin.isTTY || !process.stdout.isTTY) {
    schedule.public_contribution = current;
    fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  console.log("");
  log("Optional public contribution mode:");
  log("If enabled, this machine may prepare core NEXO improvements from an isolated checkout and open a Draft PR to the public repository.");
  log("NEXO never auto-merges, and it pauses public evolution on this machine while that Draft PR stays open.");
  log("Public contribution must never publish personal scripts, runtime data, local prompts, logs, or secrets.");
  const answer = (await ask("  Enable public contribution via Draft PRs on this machine? [y/N/later]: ")).trim().toLowerCase();
  if (answer === "y" || answer === "yes") {
    const login = ghLogin();
    if (!login) {
      current.enabled = false;
      current.mode = "pending_auth";
      current.status = "pending_auth";
      current.github_user = "";
      current.fork_repo = "";
      log("GitHub CLI authentication is missing. Contributor mode is pending until 'gh auth login' succeeds.");
    } else {
      const fork = ensureFork(login);
      if (!fork.ok) {
        current.enabled = false;
        current.mode = "pending_auth";
        current.status = "pending_auth";
        current.github_user = login;
        current.fork_repo = "";
        log(fork.message || "Could not ensure a GitHub fork.");
      } else {
        current.enabled = true;
        current.mode = "draft_prs";
        current.status = "active";
        current.github_user = login;
        current.fork_repo = fork.forkRepo;
      }
    }
  } else if (answer === "later" || answer === "l" || answer === "") {
    current.enabled = false;
    current.mode = "unset";
    current.status = "unset";
  } else {
    current.enabled = false;
    current.mode = "off";
    current.status = "off";
    current.github_user = "";
    current.fork_repo = "";
  }

  schedule.public_contribution = current;
  fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
  return schedule;
}

/**
 * Resolve the venv python path for an existing NEXO_HOME installation.
 */
function findVenvPython(nexoHome) {
  const venvPath = path.join(nexoHome, ".venv");
  const venvPy = managedVenvPythonPath(nexoHome);
  ensureManagedVenvCompatible(venvPath, venvPy);
  if (fs.existsSync(venvPy)) return venvPy;
  return null;
}

/**
 * Map day name to systemd OnCalendar day abbreviation and crontab day number.
 */
const DAY_MAP = {
  sunday: { systemd: "Sun", cron: 0 },
  monday: { systemd: "Mon", cron: 1 },
  tuesday: { systemd: "Tue", cron: 2 },
  wednesday: { systemd: "Wed", cron: 3 },
  thursday: { systemd: "Thu", cron: 4 },
  friday: { systemd: "Fri", cron: 5 },
  saturday: { systemd: "Sat", cron: 6 },
};

/**
 * Install all 13 processes on the current platform.
 * macOS: LaunchAgents (.plist)
 * Linux+systemd: .service + .timer files
 * Linux fallback: crontab entries
 */
function installAllProcesses(platform, pythonPath, nexoHome, schedule, launchAgentsDir, enabledOptionals = {}) {
  const home = require("os").homedir();
  const nexoCode = nexoHome;
  const logsDir = path.join(nexoHome, "runtime", "logs");
  fs.mkdirSync(logsDir, { recursive: true });

  // Resolve script path against the canonical runtime code tree.
  function scriptPath(proc) {
    const dir = proc.scriptDir === "root" ? runtimeCodeDir(nexoHome) : runtimeScriptsDir(nexoHome);
    return path.join(dir, proc.script);
  }

  // Resolve interpreter
  function interpreterPath(proc) {
    return proc.interpreter === "bash" ? "/bin/bash" : pythonPath;
  }

  // Get schedule overrides for daily/weekly processes
  function getSchedule(proc) {
    const sched = schedule.processes || {};
    const override = sched[proc.name] || {};
    return {
      hour: override.hour !== undefined ? override.hour : (proc.defaultHour || 0),
      minute: override.minute !== undefined ? override.minute : (proc.defaultMinute || 0),
      day: override.day || proc.defaultDay || null,
    };
  }

  if (platform === "darwin") {
    // ──── macOS: LaunchAgents ────
    fs.mkdirSync(launchAgentsDir, { recursive: true });
    let count = 0;

    for (const proc of ALL_PROCESSES) {
      // Skip macOnly processes on Linux
      if (proc.macOnly && platform !== "darwin") continue;
      // Skip optional processes that weren't enabled
      if (proc.optional && !enabledOptionals[proc.optional]) continue;

      const plistName = `com.nexo.${proc.name}.plist`;
      const plistPath = path.join(launchAgentsDir, plistName);
      const sPath = scriptPath(proc);
      const interp = interpreterPath(proc);
      const s = getSchedule(proc);
      if (proc.name === "prevent-sleep" && (schedule.power_policy || "unset") !== "always_on") {
        continue;
      }

      let scheduleBlock = "";
      if (proc.type === "keepAlive") {
        scheduleBlock = `    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>`;
      } else if (proc.type === "runAtLoad") {
        let extra = "";
        if (proc.watchPaths) {
          const paths = proc.watchPaths.map(p => p.replace("~", home));
          extra = `\n    <key>WatchPaths</key>\n    <array>\n${paths.map(p => `        <string>${p}</string>`).join("\n")}\n    </array>`;
        }
        scheduleBlock = `    <key>RunAtLoad</key>
    <true/>${extra}`;
      } else if (proc.type === "interval") {
        scheduleBlock = `    <key>StartInterval</key>
    <integer>${proc.intervalMinutes * 60}</integer>
    <key>RunAtLoad</key>
    <false/>`;
      } else if (proc.type === "daily") {
        scheduleBlock = `    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${s.hour}</integer>
        <key>Minute</key>
        <integer>${s.minute}</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>`;
      } else if (proc.type === "weekly") {
        // macOS uses Weekday 0=Sunday
        const dayNum = s.day ? (DAY_MAP[s.day.toLowerCase()] || { cron: 0 }).cron : 0;
        scheduleBlock = `    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>${dayNum}</integer>
        <key>Hour</key>
        <integer>${s.hour}</integer>
        <key>Minute</key>
        <integer>${s.minute}</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>`;
      }

      const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nexo.${proc.name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${interp}</string>
        <string>${sPath}</string>
    </array>
    ${scheduleBlock}
    <key>StandardOutPath</key>
    <string>${path.join(logsDir, `${proc.name}-stdout.log`)}</string>
    <key>StandardErrorPath</key>
    <string>${path.join(logsDir, `${proc.name}-stderr.log`)}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${home}</string>
        <key>NEXO_HOME</key>
        <string>${nexoHome}</string>
        <key>NEXO_CODE</key>
        <string>${nexoCode}</string>
        <key>PATH</key>
        <string>${resolveLaunchAgentPath(home)}</string>
    </dict>
</dict>
</plist>`;

      fs.writeFileSync(plistPath, plist);
      try {
        execSync(
          `launchctl bootout gui/$(id -u) "${plistPath}" 2>/dev/null; launchctl bootstrap gui/$(id -u) "${plistPath}"`,
          { stdio: "pipe" }
        );
      } catch {
        // May fail if not previously loaded, that's OK
      }
      count++;
    }
    log(`${count} automated processes configured (LaunchAgents).`);

  } else if (platform === "linux") {
    // ──── Linux: systemd user timers (preferred) or crontab fallback ────
    const systemdDir = path.join(home, ".config", "systemd", "user");
    const hasSystemd = run("which systemctl") && run("systemctl --user status 2>/dev/null");

    if (hasSystemd) {
      fs.mkdirSync(systemdDir, { recursive: true });
      let count = 0;

      for (const proc of ALL_PROCESSES) {
        if (proc.macOnly) continue; // tcc-approve is macOS only
        if (proc.optional && !enabledOptionals[proc.optional]) continue;
        const serviceName = `nexo-${proc.name}`;
        const serviceFile = path.join(systemdDir, `${serviceName}.service`);
        const timerFile = path.join(systemdDir, `${serviceName}.timer`);
        const sPath = scriptPath(proc);
        const interp = interpreterPath(proc);
        const s = getSchedule(proc);
        if (proc.name === "prevent-sleep" && (schedule.power_policy || "unset") !== "always_on") continue;

        const serviceType = proc.type === "keepAlive" ? "simple" : "oneshot";
        const restartPolicy = proc.type === "keepAlive" ? "Restart=always\nRestartSec=5" : "";
        const service = `[Unit]
Description=NEXO Brain — ${proc.name} (${proc.purpose})

[Service]
Type=${serviceType}
ExecStart=${interp} ${sPath}
Environment=HOME=${home}
Environment=NEXO_HOME=${nexoHome}
Environment=NEXO_CODE=${nexoCode}
StandardOutput=append:${path.join(logsDir, `${proc.name}-stdout.log`)}
StandardError=append:${path.join(logsDir, `${proc.name}-stderr.log`)}
${restartPolicy}
`;

        // Build calendar spec
        let onCalendar = "";
        let persistent = "true";
        if (proc.type === "keepAlive") {
          // KeepAlive = persistent service, no timer needed
          fs.writeFileSync(serviceFile, service + `\n[Install]\nWantedBy=default.target\n`);
          run(`systemctl --user enable ${serviceName}.service`);
          run(`systemctl --user start ${serviceName}.service`);
          count++;
          continue;
        } else if (proc.type === "runAtLoad") {
          // runAtLoad: enable as a boot-time oneshot service (like macOS RunAtLoad)
          fs.writeFileSync(serviceFile, service + `\n[Install]\nWantedBy=default.target\n`);
          run(`systemctl --user enable ${serviceName}.service`);
          run(`systemctl --user start ${serviceName}.service`);
          count++;
          continue;
        } else if (proc.type === "interval") {
          onCalendar = `*:0/${proc.intervalMinutes}`;
        } else if (proc.type === "daily") {
          onCalendar = `*-*-* ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}:00`;
        } else if (proc.type === "weekly") {
          const dayAbbr = s.day ? (DAY_MAP[s.day.toLowerCase()] || { systemd: "Sun" }).systemd : "Sun";
          onCalendar = `${dayAbbr} *-*-* ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}:00`;
        }

        const timer = `[Unit]
Description=NEXO Brain — ${proc.name} timer

[Timer]
OnCalendar=${onCalendar}
Persistent=${persistent}

[Install]
WantedBy=timers.target
`;

        fs.writeFileSync(serviceFile, service);
        fs.writeFileSync(timerFile, timer);
        try {
          execSync(`systemctl --user enable --now ${serviceName}.timer 2>/dev/null`, { stdio: "pipe" });
        } catch {}
        count++;
      }
      log(`${count} systemd user timers configured.`);

    } else {
      // ──── Fallback: crontab ────
      log("systemd not available, configuring crontab...");
      const cronLines = [];
      const envLine = `NEXO_HOME=${nexoHome}`;
      const envLine2 = `NEXO_CODE=${nexoCode}`;

      for (const proc of ALL_PROCESSES) {
        if (proc.optional && !enabledOptionals[proc.optional]) continue;
        const sPath = scriptPath(proc);
        const interp = interpreterPath(proc);
        const s = getSchedule(proc);
        const logPath = path.join(logsDir, `${proc.name}-stdout.log`);

        let cronSpec = "";
        if (proc.type === "runAtLoad") {
          cronSpec = "@reboot";
        } else if (proc.type === "interval") {
          cronSpec = `*/${proc.intervalMinutes} * * * *`;
        } else if (proc.type === "daily") {
          cronSpec = `${s.minute} ${s.hour} * * *`;
        } else if (proc.type === "weekly") {
          const dayNum = s.day ? (DAY_MAP[s.day.toLowerCase()] || { cron: 0 }).cron : 0;
          cronSpec = `${s.minute} ${s.hour} * * ${dayNum}`;
        }

        cronLines.push(`${cronSpec} ${interp} ${sPath} >> ${logPath} 2>&1`);
      }

      try {
        const existingCron = run("crontab -l 2>/dev/null") || "";
        const nexoCronMarker = "# NEXO Brain automated processes";
        const nexoCronEnd = "# END NEXO Brain";

        // Remove old NEXO cron block if present, then add fresh one
        let baseCron = existingCron;
        if (existingCron.includes(nexoCronMarker)) {
          const startIdx = existingCron.indexOf(nexoCronMarker);
          const endIdx = existingCron.indexOf(nexoCronEnd);
          if (endIdx > startIdx) {
            baseCron = existingCron.substring(0, startIdx) + existingCron.substring(endIdx + nexoCronEnd.length);
          } else {
            baseCron = existingCron.substring(0, startIdx);
          }
        }

        const newCron = baseCron.trimEnd() + "\n" + nexoCronMarker + "\n" + envLine + "\n" + envLine2 + "\n" + cronLines.join("\n") + "\n" + nexoCronEnd + "\n";
        const tmpCron = path.join(nexoHome, ".crontab-tmp");
        fs.writeFileSync(tmpCron, newCron);
        execSync(`crontab ${tmpCron}`, { stdio: "pipe" });
        fs.unlinkSync(tmpCron);
        log(`${cronLines.length} cron jobs configured.`);
      } catch (e) {
        log(`Could not configure crontab: ${e.message}`);
        log("Background tasks will run via catch-up on startup.");
      }
    }
  } else {
    log("Unsupported platform for background tasks. Maintenance runs on MCP startup.");
  }
}

function syncCoreProcessesFromManifest(pythonPath, nexoHome, sourceRoot = "") {
  const candidateSyncPaths = [
    path.join(resolveRuntimeCronsDir(nexoHome), "sync.py"),
    sourceRoot ? path.join(sourceRoot, "sync.py") : "",
  ].filter(Boolean);
  const runtimeCode = runtimeCodeDir(nexoHome);
  let lastError = "";

  for (const syncPath of candidateSyncPaths) {
    if (!fs.existsSync(syncPath)) continue;
    const syncResult = spawnSync(
      pythonPath || "python3",
      [syncPath],
      {
        env: {
          ...process.env,
          HOME: require("os").homedir(),
          NEXO_HOME: nexoHome,
          NEXO_CODE: runtimeCode,
        },
        stdio: "pipe",
        encoding: "utf8",
      },
    );
    if (syncResult.status === 0) {
      return { ok: true, syncPath };
    }
    lastError = (syncResult.stderr || syncResult.stdout || "").trim() || `exit ${syncResult.status}`;
  }

  return {
    ok: false,
    error: lastError || "cron sync script not found",
  };
}

async function runSetup() {
  // Non-interactive mode: --defaults, --yes, --skip, or -y all skip prompts
  // and apply the recommended defaults end-to-end (v6.0.0 adds --skip).
  const useDefaults = process.argv.includes("--defaults")
    || process.argv.includes("--yes")
    || process.argv.includes("--skip")
    || process.argv.includes("-y");
  const smokeTestMode = process.env.NEXO_TESTING_SMOKE === "1";

  console.log("");
  console.log(
    "  ╔══════════════════════════════════════════════════════════╗"
  );
  console.log(
    "  ║  🧠 NEXO Brain — Setup                                 ║"
  );
  console.log(
    "  ║                                                        ║"
  );
  console.log(
    "  ║  Hello! / ¡Hola! / Bonjour! / Hallo!                  ║"
  );
  console.log(
    "  ║  Ciao! / Olá! / こんにちは! / 你好!                     ║"
  );
  console.log(
    "  ╚══════════════════════════════════════════════════════════╝"
  );
  console.log("");
  if (useDefaults) {
    log("Running with defaults (non-interactive mode).");
    console.log("");
  }

  // Check prerequisites
  const platform = process.platform;
  if (platform === "win32") {
    log("Windows detected, but the automatic WSL bridge was not available.");
    log("Install WSL: https://learn.microsoft.com/en-us/windows/wsl/install");
    log("Then run this command again, or launch it directly inside WSL (Ubuntu terminal).");
    process.exit(1);
  }
  if (platform !== "darwin" && platform !== "linux") {
    log(`Unsupported platform: ${platform}. NEXO supports macOS and Linux (Windows via WSL).`);
    process.exit(1);
  }

  const bundleRoot = getRuntimeBundleRoot();
  const bundleSrcDir = path.join(bundleRoot, "src");
  const bundleTemplatesDir = path.join(bundleRoot, "templates");
  process.on("exit", () => {
    if (_stagedRuntimeCleanup) {
      _stagedRuntimeCleanup();
    }
  });

  const onboardingMigration = ensureOnboardingCompletionMarker(NEXO_HOME);
  if (onboardingMigration.changed) {
    log("Migrated legacy calibration completion marker.");
  } else {
    const calibrationRecord = readRuntimeCalibration(NEXO_HOME);
    if (hasPartialPlaceholderCalibration(calibrationRecord.payload)) {
      log("Incomplete calibration detected; restarting onboarding cleanly.");
    }
  }

  // Auto-migration: detect existing installation
  const versionFile = path.join(NEXO_HOME, "version.json");
  if (fs.existsSync(versionFile)) {
    try {
      const installed = JSON.parse(fs.readFileSync(versionFile, "utf8"));
      const currentPkg = readPackageJson();
      const installedVersion = installed.version || "0.0.0";
      const currentVersion = currentPkg.version;
      const activeRuntimeVersion = readActiveRuntimeSnapshotVersion(NEXO_HOME);
      const needsRuntimeRepair = activeRuntimeVersion !== currentVersion;

      if (installedVersion !== currentVersion || needsRuntimeRepair) {
        if (installedVersion !== currentVersion) {
          log(`Existing installation detected: v${installedVersion} → v${currentVersion}`);
          log("Running auto-migration...");
        } else {
          log(`Existing installation detected: metadata v${installedVersion}, runtime core/current v${activeRuntimeVersion || "missing"}`);
          log("Repairing active runtime snapshot...");
        }

        // Recursive copy helper (skips __pycache__, .pyc, .db files)
        const srcDir = bundleSrcDir;
        const copyDirRec = (src, dest) => {
          fs.mkdirSync(dest, { recursive: true });
          fs.readdirSync(src).forEach(item => {
            if (item === "__pycache__" || item.endsWith(".pyc") || item.endsWith(".db") || isDuplicateArtifactName(item, src)) return;
            const srcPath = path.join(src, item);
            const destPath = path.join(dest, item);
            if (fs.statSync(srcPath).isDirectory()) {
              copyDirRec(srcPath, destPath);
            } else {
              fs.copyFileSync(srcPath, destPath);
            }
          });
        };

        // Update hooks (entire directory)
        const hooksSrc = path.join(srcDir, "hooks");
        const hooksDest = path.join(NEXO_HOME, "core", "hooks");
        if (fs.existsSync(hooksSrc)) {
          copyDirRec(hooksSrc, hooksDest);
          // Make .sh files executable
          fs.readdirSync(hooksDest).filter(f => f.endsWith(".sh")).forEach(f => {
            fs.chmodSync(path.join(hooksDest, f), "755");
          });
        }
        log("  Hooks updated.");

        // Update core Python files (flat .py files in src/)
        const coreFlatFiles = getCoreRuntimeFlatFiles(srcDir);
        coreFlatFiles.forEach((f) => {
          const src = path.join(srcDir, f);
          if (fs.existsSync(src)) {
            const dest = path.join(NEXO_HOME, "core", f);
            fs.mkdirSync(path.dirname(dest), { recursive: true });
            fs.copyFileSync(src, dest);
          }
        });
        // Update core packages (db/, cognitive/) — full directory copy
        getCoreRuntimePackages().forEach(pkg => {
          const pkgSrc = path.join(srcDir, pkg);
          if (fs.existsSync(pkgSrc)) {
            copyDirRec(pkgSrc, path.join(NEXO_HOME, "core", pkg));
          }
        });
        // Publish Brain contracts to ~/.nexo/brain/ (read by NEXO Desktop et al.)
        publishBrainContracts(srcDir, NEXO_HOME);
        log("  Core files updated.");

        // Reconcile Python dependencies after updating code (mirrors fresh-install logic)
        const migReqFile = path.join(srcDir, "requirements.txt");
        if (fs.existsSync(migReqFile)) {
          const migVenvPy = findVenvPython(NEXO_HOME);
          const migPipPy = migVenvPy || "python3";
          const migPipArgs = ["-m", "pip", "install", "--quiet", "-r", migReqFile];
          if (!migVenvPy) migPipArgs.push("--break-system-packages");
          log("  Reconciling Python dependencies...");
          const migPipResult = spawnSync(migPipPy, migPipArgs, { stdio: "inherit", timeout: 120000 });
          if (migPipResult.status !== 0) {
            log("  WARNING: Failed to reconcile Python deps. Rolling back version...");
            // Restore previous version so next boot retries migration
            fs.writeFileSync(versionFile, JSON.stringify({
              version: installedVersion,
              installed_at: installed.installed_at,
              updated_at: new Date().toISOString(),
              migration_failed: currentVersion,
            }, null, 2));
            log("  Run manually: " + migPipPy + " -m pip install -r src/requirements.txt");
            process.exit(1);
          }
          log("  Python dependencies reconciled.");
        }

        const migPythonForWarmup = findVenvPython(NEXO_HOME) || "python3";
        runDesktopAwareModelWarmup(migPythonForWarmup, NEXO_HOME, { reason: "update", installRuntimeDeps: false });

        // Update plugins (all .py files in plugins/)
        const pluginsSrc = path.join(srcDir, "plugins");
        const pluginsDest = path.join(NEXO_HOME, "core", "plugins");
        fs.mkdirSync(pluginsDest, { recursive: true });
        if (fs.existsSync(pluginsSrc)) {
          fs.readdirSync(pluginsSrc).filter(f => f.endsWith(".py") && !isDuplicateArtifactName(f, pluginsSrc)).forEach((f) => {
            fs.copyFileSync(path.join(pluginsSrc, f), path.join(pluginsDest, f));
          });
        }
        log("  Plugins updated.");

        // Update dashboard (recursive — includes static/, templates/)
        const dashSrc = path.join(srcDir, "dashboard");
        const dashDest = path.join(NEXO_HOME, "core", "dashboard");
        if (fs.existsSync(dashSrc)) {
          copyDirRec(dashSrc, dashDest);
          log("  Dashboard updated.");
        }

        // Update rules (directory with core-rules.json, __init__.py, migrate.py)
        const rulesSrc = path.join(srcDir, "rules");
        const rulesDest = path.join(NEXO_HOME, "core", "rules");
        if (fs.existsSync(rulesSrc)) {
          copyDirRec(rulesSrc, rulesDest);
          log("  Rules updated.");
        }

        // Update crons (manifest.json + sync.py — needed by catchup & watchdog)
        const cronsMigSrc = path.join(srcDir, "crons");
        const cronsMigDest = path.join(NEXO_HOME, "runtime", "crons");
        if (fs.existsSync(cronsMigSrc)) {
          copyDirRec(cronsMigSrc, cronsMigDest);
          log("  Crons updated.");
        }

        // Update scripts (all .py, .sh files + subdirectories like deep-sleep/)
        const scriptsSrc = path.join(srcDir, "scripts");
        const scriptsDest = path.join(NEXO_HOME, "core", "scripts");
        if (fs.existsSync(scriptsSrc)) {
          copyDirRec(scriptsSrc, scriptsDest);
          // Make .sh files executable
          fs.readdirSync(scriptsDest).filter(f => f.endsWith(".sh")).forEach(f => {
            fs.chmodSync(path.join(scriptsDest, f), "755");
          });
        }
        writeRuntimeCoreArtifactsManifest(NEXO_HOME, srcDir);
        log("  Scripts updated.");

        // Update templates/ root (core-prompts/, CLAUDE.md.template, etc.) — recursive
        // Managed surface: copyDirRec overwrites without diffing, so any
        // hand-edited template under ~/.nexo/templates/ is replaced on
        // upgrade. Keep local forks under personal/ or outside the runtime
        // home to avoid silent loss.
        const migTemplatesSrc = bundleTemplatesDir;
        const migTemplatesDest = path.join(NEXO_HOME, "templates");
        if (fs.existsSync(migTemplatesSrc)) {
          copyDirRec(migTemplatesSrc, migTemplatesDest);
          log("  Templates updated (user-edited templates/ files are overwritten).");
        }

        // Register ALL 8 core hooks in settings.json (additive — don't remove user's custom hooks)
        let settings = {};
        if (fs.existsSync(CLAUDE_SETTINGS)) {
          try { settings = JSON.parse(fs.readFileSync(CLAUDE_SETTINGS, "utf8")); } catch {}
        }
        if (!settings.hooks) settings.hooks = {};
        const migHooksDest = path.join(NEXO_HOME, "core", "hooks");
        registerAllCoreHooks(settings, migHooksDest, NEXO_HOME);
        fs.mkdirSync(path.dirname(CLAUDE_SETTINGS), { recursive: true });
        fs.writeFileSync(CLAUDE_SETTINGS, JSON.stringify(settings, null, 2));
        log("  All 8 core hooks registered in Claude Code settings.");

        // Regenerate all core LaunchAgents / systemd timers
        let migSchedule = loadOrCreateSchedule(NEXO_HOME);
        migSchedule = await maybeConfigurePowerPolicy(migSchedule, useDefaults);
        migSchedule = await maybeConfigurePublicContribution(migSchedule, useDefaults);
        const migPython = findVenvPython(NEXO_HOME) || "python3";
        migSchedule = await maybeConfigureFullDiskAccess(migSchedule, useDefaults, migPython);
        let migOptionals = {};
        try {
          const optFile = path.join(resolveRuntimeConfigDir(NEXO_HOME), "optionals.json");
          if (fs.existsSync(optFile)) migOptionals = JSON.parse(fs.readFileSync(optFile, "utf8"));
        } catch {}
        const migCronSync = syncCoreProcessesFromManifest(migPython, NEXO_HOME, cronsMigSrc);
        if (migCronSync.ok) {
          log("  Core crons reconciled with manifest.");
        } else {
          log(`  Cron sync warning: ${migCronSync.error}. Falling back to legacy installer.`);
          installAllProcesses(platform, migPython, NEXO_HOME, migSchedule, LAUNCH_AGENTS, migOptionals);
          log("  Automated processes updated via legacy installer fallback.");
        }

        // Update version file
        fs.writeFileSync(versionFile, JSON.stringify({
          version: currentVersion,
          installed_at: installed.installed_at,
          updated_at: new Date().toISOString(),
          migrated_from: installedVersion,
          ...(installedVersion === currentVersion && activeRuntimeVersion && activeRuntimeVersion !== currentVersion
            ? { runtime_repaired_from: activeRuntimeVersion }
            : {}),
        }, null, 2));
        syncRuntimePackageMetadata(bundleRoot, NEXO_HOME);
        log("Finalizing F0.6 runtime layout...");
        const migLayoutFinalize = finalizeF06Layout(migPython, NEXO_HOME);
        if (!migLayoutFinalize.ok) {
          throw new Error(`F0.6 layout finalization failed: ${migLayoutFinalize.error}`);
        }
        const migActivation = activateVersionedRuntimeSnapshot(migPython, NEXO_HOME, currentVersion);
        if (!migActivation.ok) {
          throw new Error(`Runtime activation failed: ${migActivation.error}`);
        }
        log(`  Runtime activation: core/current -> versions/${currentVersion}`);

        // Keep the rendered template in-memory for version tracking, but do
        // not drop a loose reference file in NEXO_HOME root.
        const templateSrc = path.join(bundleTemplatesDir, "CLAUDE.md.template");
        if (fs.existsSync(templateSrc)) {
          const operatorName = installed.operator_name || DEFAULT_ASSISTANT_NAME;
          let claudeMd = fs.readFileSync(templateSrc, "utf8")
            .replace(/\{\{NAME\}\}/g, operatorName)
            .replace(/\{\{NEXO_HOME\}\}/g, NEXO_HOME);

          // Update CLAUDE.md version tracker (auto_update.py will handle section migration on next server start)
          const migClaudeMdVerMatch = claudeMd.match(/nexo-claude-md-version:\s*([\d.]+)/);
          if (migClaudeMdVerMatch) {
            const migDataDir = resolveRuntimeDataDir(NEXO_HOME);
            fs.mkdirSync(migDataDir, { recursive: true });
            // Don't write the version yet — let auto_update.py detect the diff and migrate sections
            // Only write if no version file exists (first time with version tracking)
            const migVerFile = path.join(migDataDir, "claude_md_version.txt");
            if (!fs.existsSync(migVerFile)) {
              fs.writeFileSync(migVerFile, "0.0.0");
              log(`  CLAUDE.md version tracker initialized (will migrate on next server start)`);
            }
          }
        }

        // Restore operator shell alias + PATH if lost during previous updates
        const migOperatorName = installed.operator_name || DEFAULT_ASSISTANT_NAME;
        const migAliasName = migOperatorName.toLowerCase();
        if (migAliasName !== "nexo") {
          const migSkip = shouldSkipShellProfileBackfill();
          if (migSkip.skip) {
            log(`  Skipping shell profile alias restore — ${migSkip.reason}`);
          } else {
          const migAliasLine = `alias ${migAliasName}='nexo chat .'`;
          const migAliasComment = `# ${migOperatorName} — open the configured NEXO terminal client`;
          const migNexoPathLine = `export PATH="${path.join(NEXO_HOME, "bin")}:$PATH"`;
          const migNexoPathComment = "# NEXO runtime CLI";
          const migUserShell = process.env.SHELL || "/bin/bash";
          const migHomeDir = require("os").homedir();
          const migRcFiles = [];
          if (migUserShell.includes("zsh")) {
            migRcFiles.push(path.join(migHomeDir, ".zshrc"));
          } else {
            migRcFiles.push(path.join(migHomeDir, ".bash_profile"));
            migRcFiles.push(path.join(migHomeDir, ".bashrc"));
          }
          for (const rcFile of migRcFiles) {
            let rcContent = "";
            if (fs.existsSync(rcFile)) {
              rcContent = fs.readFileSync(rcFile, "utf8");
            }
            if (!rcContent.includes(migNexoPathLine)) {
              fs.appendFileSync(rcFile, `\n${migNexoPathComment}\n${migNexoPathLine}\n`);
              log(`  Restored NEXO runtime CLI in ${path.basename(rcFile)}`);
              rcContent += `\n${migNexoPathComment}\n${migNexoPathLine}\n`;
            }
            if (!rcContent.includes(`alias ${migAliasName}=`)) {
              fs.appendFileSync(rcFile, `\n${migAliasComment}\n${migAliasLine}\n`);
              log(`  Restored '${migAliasName}' alias in ${path.basename(rcFile)}`);
            }
          }
          }
        }

        console.log("");
        log(`Migration complete: v${installedVersion} → v${currentVersion}`);
        log("Your data (memories, learnings, preferences) is untouched.");
        console.log("");
        closeReadline();
        return;
      }

      // Same version — backfill crons/ if missing (for installs before crons was shipped)
      const syncPython = findVenvPython(NEXO_HOME) || run("which python3") || "python3";
      const cronsDest = resolveRuntimeCronsDir(NEXO_HOME);
      const cronsSrc = path.join(bundleSrcDir, "crons");
      if (fs.existsSync(cronsSrc)) {
        const copyDirRec2 = (src, dest) => {
          fs.mkdirSync(dest, { recursive: true });
          fs.readdirSync(src).forEach(item => {
            if (item === "__pycache__" || item.endsWith(".pyc") || item.endsWith(".db") || isDuplicateArtifactName(item, src)) return;
            const srcP = path.join(src, item);
            const destP = path.join(dest, item);
            if (fs.statSync(srcP).isDirectory()) copyDirRec2(srcP, destP);
            else fs.copyFileSync(srcP, destP);
          });
        };
        copyDirRec2(cronsSrc, cronsDest);
        log("Refreshed crons/ directory.");

        const syncStatus = syncCoreProcessesFromManifest(syncPython, NEXO_HOME, cronsSrc);
        if (syncStatus.ok) {
          log("Core crons reconciled with manifest.");
        } else {
          log(`Cron sync warning: ${syncStatus.error}`);
        }
      }

      // Same version — refresh packaged core skills/templates/runtime helpers too.
      const skillsCoreDest = path.join(NEXO_HOME, "core", "skills");
      const skillsCoreSrc = path.join(bundleSrcDir, "skills");
      if (fs.existsSync(skillsCoreSrc)) {
        const copyDirRec3 = (src, dest) => {
          fs.mkdirSync(dest, { recursive: true });
          fs.readdirSync(src).forEach(item => {
            if (item === "__pycache__" || item.endsWith(".pyc") || isDuplicateArtifactName(item, src)) return;
            const srcP = path.join(src, item);
            const destP = path.join(dest, item);
            if (fs.statSync(srcP).isDirectory()) copyDirRec3(srcP, destP);
            else fs.copyFileSync(srcP, destP);
          });
        };
        copyDirRec3(skillsCoreSrc, skillsCoreDest);
        log("Refreshed skills-core/ directory.");
      }

      ["skills_runtime.py"].forEach((fname) => {
        const srcFile = path.join(bundleSrcDir, fname);
        const destFile = path.join(NEXO_HOME, "core", fname);
        if (fs.existsSync(srcFile)) {
          fs.mkdirSync(path.dirname(destFile), { recursive: true });
          fs.copyFileSync(srcFile, destFile);
        }
      });
      syncRuntimePackageMetadata(bundleRoot, NEXO_HOME);

      const templatesSrc = bundleTemplatesDir;
      const templatesDest = path.join(NEXO_HOME, "templates");
      if (fs.existsSync(templatesSrc)) {
        fs.mkdirSync(templatesDest, { recursive: true });
        for (const f of fs.readdirSync(templatesSrc)) {
          if (isDuplicateArtifactName(f, templatesSrc)) continue;
          const src = path.join(templatesSrc, f);
          const dest = path.join(templatesDest, f);
          if (fs.statSync(src).isFile()) {
            fs.copyFileSync(src, dest);
          } else if (fs.statSync(src).isDirectory()) {
            fs.mkdirSync(dest, { recursive: true });
            for (const sf of fs.readdirSync(src)) {
              if (isDuplicateArtifactName(sf, src)) continue;
              const ssrc = path.join(src, sf);
              if (fs.statSync(ssrc).isFile()) {
                fs.copyFileSync(ssrc, path.join(dest, sf));
              }
            }
          }
        }
      }
      log("Finalizing F0.6 runtime layout...");
      const syncLayoutFinalize = finalizeF06Layout(syncPython, NEXO_HOME);
      if (!syncLayoutFinalize.ok) {
        throw new Error(`F0.6 layout finalization failed: ${syncLayoutFinalize.error}`);
      }

      runDesktopAwareModelWarmup(syncPython, NEXO_HOME, { reason: "repair" });
      logMacPermissionsNotice(NEXO_HOME, syncPython);

      log(`Already at v${currentVersion}. No migration needed.`);

      // Ensure bundled Claude Code is installed even when migration is skipped.
      try {
        const _claudeCheck = detectInstalledClients().claude_code;
        if (!_claudeCheck.installed) {
          const _bundledClaudeDir = path.join(__dirname, "..", "claude-code");
          if (fs.existsSync(_bundledClaudeDir)) {
            const _tgzFiles = fs.readdirSync(_bundledClaudeDir).filter((f) => f.endsWith(".tgz"));
            if (_tgzFiles.length > 0) {
              log("Bundled Claude Code tarball detected after migration-skip — installing offline.");
              installClaudeCodeCli(process.platform);
            }
          }
        }
      } catch (_e) {}

      closeReadline();
      return;
    } catch (e) {
      // Version file corrupt — proceed with fresh install
    }
  }

  // Find or install Python (platform-aware)
  let python = resolveInstallerPython();
  if (!python) {
    if (platform === "darwin") {
      // v0.32.5 — Mac vanilla NO trae python3. La auto-instalación de
      // Homebrew vía `curl install.sh` requiere TTY interactivo + sudo +
      // user accept license. Cuando este script se invoca desde Electron
      // sandbox, NO hay TTY → curl pipe cuelga sin progreso → bootstrap
      // se queda silencioso. Mejor: detectar la ausencia de Python y
      // surface un error CLARO al user con instrucciones manuales que
      // funcionan siempre. Si hay TTY (corriendo desde terminal), seguimos
      // con el path automático.
      const isTty = !!(process.stdin && process.stdin.isTTY);
      let hasBrew = run("which brew");
      if (!hasBrew && !isTty) {
        log("ERROR: Python 3.12 is not installed and the auto-installer requires an interactive terminal.");
        log("");
        log("Please install Python 3.12 manually:");
        log("  1. Open Terminal.app");
        log("  2. Run: xcode-select --install");
        log("  3. Run: /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"");
        log("  4. Run: brew install python@3.12");
        log("  5. Reopen NEXO Desktop");
        log("");
        log("More help: https://nexo-desktop.com/help/python-install");
        process.exit(1);
      }
      if (!hasBrew) {
        log("Homebrew not found. Installing...");
        spawnSync("/bin/bash", ["-c", '$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)'], {
          stdio: "inherit",
        });
        hasBrew = run("which brew") || run("eval $(/opt/homebrew/bin/brew shellenv) && which brew");
      }
      if (hasBrew) {
        // v0.32.5 — explicit @3.12 pin: el Brain `requirements.txt` y los
        // wheels manylinux están compilados contra cp312. `brew install
        // python3` instala el `python3` formula que actualmente apunta a
        // 3.13 → wheels rechazados → numpy/cffi/cryptography/onnxruntime
        // fallan al import. Pinning a `python@3.12` evita el drift.
        log("Python 3.12 not found. Installing via Homebrew...");
        spawnSync("brew", ["install", "python@3.12"], { stdio: "inherit" });
        python = resolveInstallerPython() || run("which python3.12") || run("which python3");
      }
    } else if (platform === "linux") {
      // Linux: try apt or yum
      log("Python 3 not found. Attempting install...");
      if (run("which apt-get")) {
        spawnSync("sudo", ["apt-get", "install", "-y", "python3", "python3-pip", "python3-venv"], { stdio: "inherit" });
      } else if (run("which yum")) {
        spawnSync("sudo", ["yum", "install", "-y", "python3", "python3-pip"], { stdio: "inherit" });
      }
      python = resolveInstallerPython();
    }
    if (!python) {
      log("Python 3 not found and couldn't install automatically.");
      log(platform === "darwin" ? "Install it: brew install python3" : "Install it: sudo apt install python3");
      process.exit(1);
    }
  }
  const pyVersion = pythonVersion(python);
  if (!pyVersion || !pythonVersionMeetsMinimum(pyVersion)) {
    log(pyVersion
      ? `Python at ${python} is ${pyVersion}; NEXO Brain requires Python >=${MIN_INSTALLER_PYTHON_MAJOR}.${MIN_INSTALLER_PYTHON_MINOR}.`
      : `Python at ${python || "(not found)"} is not executable.`);
    process.exit(1);
  }
  log(`Found ${pyVersion} at ${python}`);
  logMacPermissionsNotice(NEXO_HOME, python);

  let detectedClients = detectInstalledClients();
  log(
    `Client detection: Claude Code=${detectedClients.claude_code.installed ? "yes" : "no"}, `
    + `Codex=${detectedClients.codex.installed ? "yes" : "no"}, `
    + `Claude Desktop=${detectedClients.claude_desktop.installed ? "yes" : "no"}`
  );
  console.log("");

  // Step 1: Language (P1)
  // Language-specific strings for the entire onboarding
  const i18n = {
    en: {
      langConfirm: "English it is.",
      askDataDir: `  Where should I store my data? (databases, backups, personal plugins)\n  Default: ~/.nexo/\n  > `,
      dataDirConfirm: (p) => `Data directory: ${p}`,
      askUserName: "  What's your name? > ",
      userGreet: (n) => `Nice to meet you, ${n}.`,
      askAgentName: `  What should I call myself? (default: ${DEFAULT_ASSISTANT_NAME}) > `,
      agentConfirm: (n) => `Got it. I'm ${n}.`,
      agentNameReserved: "That name is reserved for the product. Pick a different assistant name.",
      calibTitle: "Let's calibrate my personality to work best with you.",
      calibNote: "(You can change these anytime via nexo_preference_set)",
      autonomyQ: "  How autonomous should I be?\n    1. Conservative — ask before most actions\n    2. Balanced — act on routine, ask on important\n    3. Full — act first, inform after, only ask when truly uncertain\n  > ",
      commQ: "  How should I communicate?\n    1. Concise — just results, zero filler\n    2. Balanced — brief explanations when useful\n    3. Detailed — reasoning and trade-offs included\n  > ",
      honestyQ: "  When I disagree with your approach:\n    1. Tell you straight and explain why\n    2. Mention it briefly but follow your lead\n    3. Just do what you ask\n  > ",
      proactiveQ: "  How proactive should I be?\n    1. Only do what you ask\n    2. Suggest improvements when I spot them\n    3. Fix things I notice without asking and propose optimizations\n  > ",
      errorQ: "  When I make a mistake:\n    1. Quick fix and move on\n    2. Explain what went wrong and what I learned\n  > ",
      scanQ: "  Want me to analyze your environment to get to know you deeply?\n  Everything stays local, nothing leaves your machine.\n\n    1. Yes, analyze everything\n    2. No, I'll tell you over time\n  > ",
      scanStart: "Getting to know you... this takes 1-2 minutes.",
      scanDone: "Done.",
      caffeinateQ: "  Enable the Mac power helper for my background processes?\n  (Uses caffeinate. Closed-lid operation depends on your setup; wake recovery stays active.)\n    1. Yes\n    2. No\n  > ",
      caffYes: "Power helper enabled.",
      caffNo: "Ok, wake recovery will cover missed windows.",
      dashboardQ: "  Enable web dashboard at localhost:6174?\n  (Always-on UI to explore memory, sessions, learnings, and system health)\n    1. Yes\n    2. No\n  > ",
      dashYes: "Dashboard enabled.",
      dashNo: "Dashboard disabled. You can start it manually: nexo dashboard",
      autoInstallQ: "  Can I install tools automatically if I need them? (brew, pip, npm)\n    1. Yes, install whatever you need\n    2. Ask me before installing anything\n  > ",
      autoInstallYes: "Auto-install enabled.",
      autoInstallNo: "I'll ask before installing.",
      installing: "Configuring...",
      ready: (name, alias) => `${name} is ready. Open a new terminal and type: ${alias}`,
      readySubtext: "First time we talk, I'll finish getting to know you\nwith a couple of questions I can't figure out on my own.",
      profileTitle: "PROFILE",
    },
    es: {
      langConfirm: "Español, perfecto.",
      askDataDir: `  ¿Dónde quieres que guarde mis datos? (bases de datos, backups, plugins personales)\n  Por defecto: ~/.nexo/\n  > `,
      dataDirConfirm: (p) => `Directorio de datos: ${p}`,
      askUserName: "  ¿Cómo te llamas? > ",
      userGreet: (n) => `Encantado, ${n}.`,
      askAgentName: `  ¿Cómo quieres que me llame? (default: ${DEFAULT_ASSISTANT_NAME}) > `,
      agentConfirm: (n) => `Perfecto, soy ${n}.`,
      agentNameReserved: "Ese nombre está reservado para el producto. Elige otro nombre para el asistente.",
      calibTitle: "Vamos a calibrar mi personalidad para trabajar mejor contigo.",
      calibNote: "(Puedes cambiar esto en cualquier momento con nexo_preference_set)",
      autonomyQ: "  ¿Cuánta autonomía me das?\n    1. Conservador — pregunto antes de casi todo\n    2. Equilibrado — actúo en lo rutinario, pregunto en lo importante\n    3. Total — actúo primero, informo después, solo pregunto si hay duda real\n  > ",
      commQ: "\n  ¿Cómo prefieres que me comunique?\n    1. Conciso — solo resultados, cero relleno\n    2. Equilibrado — explicaciones breves cuando aporten\n    3. Detallado — razonamiento y trade-offs incluidos\n  > ",
      honestyQ: "\n  Cuando no esté de acuerdo con tu enfoque:\n    1. Te lo digo claro y explico por qué\n    2. Lo menciono brevemente pero sigo tu criterio\n    3. Ejecuto lo que pides sin más\n  > ",
      proactiveQ: "\n  ¿Qué tan proactivo quieres que sea?\n    1. Solo hago lo que me pidas\n    2. Sugiero mejoras cuando las detecto\n    3. Arreglo lo que veo sin preguntar y propongo optimizaciones\n  > ",
      errorQ: "\n  Cuando me equivoque:\n    1. Corrijo rápido y sigo\n    2. Explico qué falló y qué aprendí\n  > ",
      scanQ: "  ¿Quieres que analice tu entorno para conocerte a fondo?\n  Todo queda en local, nada sale de tu máquina.\n\n    1. Sí, analiza todo\n    2. No, ya te iré contando\n  > ",
      scanStart: "Conociéndote... esto toma 1-2 minutos.",
      scanDone: "Listo.",
      caffeinateQ: "  ¿Activo el helper de energía del Mac para mis procesos en segundo plano?\n  (Usa caffeinate. Con la tapa cerrada depende de tu setup; la recuperación al despertar sigue activa.)\n    1. Sí\n    2. No\n  > ",
      caffYes: "Helper de energía activado.",
      caffNo: "Ok, la recuperación al despertar cubrirá las ventanas perdidas.",
      dashboardQ: "  ¿Activar el dashboard web en localhost:6174?\n  (UI siempre activa para explorar memoria, sesiones, learnings y salud del sistema)\n    1. Sí\n    2. No\n  > ",
      dashYes: "Dashboard activado.",
      dashNo: "Dashboard desactivado. Puedes iniciarlo manualmente: nexo dashboard",
      autoInstallQ: "  ¿Puedo instalar herramientas automáticamente si las necesito? (brew, pip, npm)\n    1. Sí, instala lo que necesites\n    2. Pregúntame antes de instalar algo\n  > ",
      autoInstallYes: "Auto-instalación activada.",
      autoInstallNo: "Te preguntaré antes.",
      installing: "Configurando...",
      ready: (name, alias) => `${name} está listo. Abre una terminal nueva y escribe: ${alias}`,
      readySubtext: "La primera vez que hablemos, terminaré de conocerte\ncon un par de preguntas que no puedo resolver solo.",
      profileTitle: "PERFIL",
    },
    fr: {
      langConfirm: "Français, parfait.",
      askDataDir: `  Où stocker mes données ? (bases de données, sauvegardes, plugins)\n  Par défaut : ~/.nexo/\n  > `,
      dataDirConfirm: (p) => `Répertoire de données : ${p}`,
      askUserName: "  Comment tu t'appelles ? > ",
      userGreet: (n) => `Enchanté, ${n}.`,
      askAgentName: `  Comment veux-tu m'appeler ? (défaut : ${DEFAULT_ASSISTANT_NAME}) > `,
      agentConfirm: (n) => `C'est noté. Je suis ${n}.`,
      agentNameReserved: "Ce nom est réservé au produit. Choisis un autre nom pour l'assistant.",
      calibTitle: "Calibrons ma personnalité pour mieux travailler ensemble.",
      calibNote: "(Tu peux changer ça à tout moment avec nexo_preference_set)",
      autonomyQ: "  Quel niveau d'autonomie me donnes-tu ?\n    1. Conservateur — je demande avant presque tout\n    2. Équilibré — j'agis en routine, je demande pour l'important\n    3. Total — j'agis d'abord, j'informe après\n  > ",
      commQ: "\n  Comment préfères-tu que je communique ?\n    1. Concis — résultats seulement\n    2. Équilibré — brèves explications quand c'est utile\n    3. Détaillé — raisonnement et compromis inclus\n  > ",
      honestyQ: "\n  Quand je ne suis pas d'accord :\n    1. Je te le dis clairement\n    2. Je le mentionne brièvement\n    3. J'exécute sans commenter\n  > ",
      proactiveQ: "\n  Quel niveau de proactivité ?\n    1. Seulement ce qui est demandé\n    2. Je suggère des améliorations\n    3. Je corrige ce que je vois et propose des optimisations\n  > ",
      errorQ: "\n  Quand je me trompe :\n    1. Correction rapide\n    2. J'explique ce qui s'est passé\n  > ",
      scanQ: "  Veux-tu que j'analyse ton environnement pour te connaître en profondeur ?\n  Tout reste local.\n\n    1. Oui, analyse tout\n    2. Non, je te raconterai\n  > ",
      scanStart: "Je fais connaissance... ça prend 1-2 minutes.",
      scanDone: "Terminé.",
      caffeinateQ: "  Activer l'aide énergie du Mac pour mes processus en arrière-plan ?\n  (Utilise caffeinate. Avec le capot fermé, cela dépend de votre configuration ; la reprise au réveil reste active.)\n    1. Oui\n    2. Non\n  > ",
      caffYes: "Aide énergie activée.",
      caffNo: "D'accord, la reprise au réveil couvrira les fenêtres manquées.",
      dashboardQ: "  Activer le dashboard web sur localhost:6174 ?\n  (UI toujours active pour explorer mémoire, sessions et santé du système)\n    1. Oui\n    2. Non\n  > ",
      dashYes: "Dashboard activé.",
      dashNo: "Dashboard désactivé. Démarrage manuel : nexo dashboard",
      autoInstallQ: "  Puis-je installer des outils automatiquement ? (brew, pip, npm)\n    1. Oui\n    2. Demande-moi avant\n  > ",
      autoInstallYes: "Auto-installation activée.",
      autoInstallNo: "Je demanderai avant.",
      installing: "Configuration...",
      ready: (name, alias) => `${name} est prêt. Ouvre un nouveau terminal et tape : ${alias}`,
      readySubtext: "La première fois qu'on se parle, je finirai de te connaître\navec quelques questions que je ne peux pas résoudre seul.",
      profileTitle: "PROFIL",
    },
    de: {
      langConfirm: "Deutsch, perfekt.",
      askDataDir: `  Wo sollen meine Daten gespeichert werden? (Datenbanken, Backups, Plugins)\n  Standard: ~/.nexo/\n  > `,
      dataDirConfirm: (p) => `Datenverzeichnis: ${p}`,
      askUserName: "  Wie heißt du? > ",
      userGreet: (n) => `Freut mich, ${n}.`,
      askAgentName: `  Wie soll ich heißen? (Standard: ${DEFAULT_ASSISTANT_NAME}) > `,
      agentConfirm: (n) => `Alles klar. Ich bin ${n}.`,
      agentNameReserved: "Dieser Name ist für das Produkt reserviert. Bitte wähle einen anderen Assistentennamen.",
      calibTitle: "Kalibrieren wir meine Persönlichkeit für die Zusammenarbeit.",
      calibNote: "(Jederzeit änderbar mit nexo_preference_set)",
      autonomyQ: "  Wie viel Autonomie gibst du mir?\n    1. Konservativ — frage vor fast allem\n    2. Ausgewogen — handle bei Routine, frage bei Wichtigem\n    3. Voll — handle zuerst, informiere danach\n  > ",
      commQ: "\n  Wie soll ich kommunizieren?\n    1. Knapp — nur Ergebnisse\n    2. Ausgewogen — kurze Erklärungen wenn nützlich\n    3. Detailliert — Begründungen und Abwägungen\n  > ",
      honestyQ: "\n  Wenn ich nicht einverstanden bin:\n    1. Sage es klar\n    2. Erwähne es kurz\n    3. Führe einfach aus\n  > ",
      proactiveQ: "\n  Wie proaktiv soll ich sein?\n    1. Nur was gefragt wird\n    2. Verbesserungen vorschlagen\n    3. Selbst korrigieren und optimieren\n  > ",
      errorQ: "\n  Wenn ich einen Fehler mache:\n    1. Schnell korrigieren\n    2. Erklären was schiefging\n  > ",
      scanQ: "  Soll ich deine Umgebung analysieren um dich kennenzulernen?\n  Alles bleibt lokal.\n\n    1. Ja, analysiere alles\n    2. Nein, ich erzähle dir mit der Zeit\n  > ",
      scanStart: "Lerne dich kennen... dauert 1-2 Minuten.",
      scanDone: "Fertig.",
      caffeinateQ: "  Den Mac-Energiehelfer für meine Hintergrundprozesse aktivieren?\n  (Nutzt caffeinate. Bei geschlossenem Deckel hängt das vom Setup ab; Wiederaufnahme beim Aufwachen bleibt aktiv.)\n    1. Ja\n    2. Nein\n  > ",
      caffYes: "Energiehelfer aktiviert.",
      caffNo: "Okay, die Wiederaufnahme beim Aufwachen deckt verpasste Fenster ab.",
      dashboardQ: "  Web-Dashboard auf localhost:6174 aktivieren?\n  (Immer aktive UI für Speicher, Sitzungen und Systemgesundheit)\n    1. Ja\n    2. Nein\n  > ",
      dashYes: "Dashboard aktiviert.",
      dashNo: "Dashboard deaktiviert. Manuell starten: nexo dashboard",
      autoInstallQ: "  Darf ich Tools automatisch installieren? (brew, pip, npm)\n    1. Ja\n    2. Frag mich vorher\n  > ",
      autoInstallYes: "Auto-Installation aktiviert.",
      autoInstallNo: "Frage vorher.",
      installing: "Konfiguriere...",
      ready: (name, alias) => `${name} ist bereit. Öffne ein neues Terminal und tippe: ${alias}`,
      readySubtext: "Beim ersten Gespräch stelle ich noch ein paar Fragen\ndie ich nicht alleine beantworten kann.",
      profileTitle: "PROFIL",
    },
    it: {
      langConfirm: "Italiano, perfetto.",
      askDataDir: `  Dove salvare i miei dati? (database, backup, plugin)\n  Default: ~/.nexo/\n  > `,
      dataDirConfirm: (p) => `Directory dati: ${p}`,
      askUserName: "  Come ti chiami? > ",
      userGreet: (n) => `Piacere, ${n}.`,
      askAgentName: `  Come vuoi chiamarmi? (default: ${DEFAULT_ASSISTANT_NAME}) > `,
      agentConfirm: (n) => `Perfetto, sono ${n}.`,
      agentNameReserved: "Quel nome è riservato al prodotto. Scegli un altro nome per l'assistente.",
      calibTitle: "Calibriamo la mia personalità per lavorare meglio insieme.",
      calibNote: "(Puoi cambiare in qualsiasi momento con nexo_preference_set)",
      autonomyQ: "  Quanta autonomia mi dai?\n    1. Conservatore — chiedo prima di quasi tutto\n    2. Equilibrato — agisco nella routine, chiedo per le cose importanti\n    3. Totale — agisco prima, informo dopo\n  > ",
      commQ: "\n  Come preferisci che comunichi?\n    1. Conciso — solo risultati\n    2. Equilibrato — brevi spiegazioni quando utili\n    3. Dettagliato — ragionamento e compromessi\n  > ",
      honestyQ: "\n  Quando non sono d'accordo:\n    1. Te lo dico chiaramente\n    2. Lo accenno brevemente\n    3. Eseguo senza commentare\n  > ",
      proactiveQ: "\n  Quanto proattivo vuoi che sia?\n    1. Solo quello che chiedi\n    2. Suggerisco miglioramenti\n    3. Correggo quello che vedo e propongo ottimizzazioni\n  > ",
      errorQ: "\n  Quando sbaglio:\n    1. Correggo veloce e vado avanti\n    2. Spiego cosa è andato storto\n  > ",
      scanQ: "  Vuoi che analizzi il tuo ambiente per conoscerti a fondo?\n  Tutto resta locale.\n\n    1. Sì, analizza tutto\n    2. No, ti racconterò col tempo\n  > ",
      scanStart: "Ti conosco... ci vogliono 1-2 minuti.",
      scanDone: "Fatto.",
      caffeinateQ: "  Attivare l'helper energetico del Mac per i processi in background?\n  (Usa caffeinate. Con il coperchio chiuso dipende dal setup; il recupero al risveglio resta attivo.)\n    1. Sì\n    2. No\n  > ",
      caffYes: "Helper energetico attivato.",
      caffNo: "Ok, il recupero al risveglio coprirà le finestre perse.",
      dashboardQ: "  Attivare la dashboard web su localhost:6174?\n  (UI sempre attiva per esplorare memoria, sessioni e salute del sistema)\n    1. Sì\n    2. No\n  > ",
      dashYes: "Dashboard attivata.",
      dashNo: "Dashboard disattivata. Avvio manuale: nexo dashboard",
      autoInstallQ: "  Posso installare strumenti automaticamente? (brew, pip, npm)\n    1. Sì\n    2. Chiedimi prima\n  > ",
      autoInstallYes: "Auto-installazione attivata.",
      autoInstallNo: "Chiederò prima.",
      installing: "Configurazione...",
      ready: (name, alias) => `${name} è pronto. Apri un nuovo terminale e scrivi: ${alias}`,
      readySubtext: "La prima volta che parliamo, finirò di conoscerti\ncon un paio di domande che non posso risolvere da solo.",
      profileTitle: "PROFILO",
    },
    pt: {
      langConfirm: "Português, perfeito.",
      askDataDir: `  Onde guardar os meus dados? (bases de dados, backups, plugins)\n  Padrão: ~/.nexo/\n  > `,
      dataDirConfirm: (p) => `Diretório de dados: ${p}`,
      askUserName: "  Como te chamas? > ",
      userGreet: (n) => `Prazer, ${n}.`,
      askAgentName: `  Como queres que eu me chame? (padrão: ${DEFAULT_ASSISTANT_NAME}) > `,
      agentConfirm: (n) => `Perfeito, sou ${n}.`,
      agentNameReserved: "Esse nome está reservado para o produto. Escolhe outro nome para o assistente.",
      calibTitle: "Vamos calibrar a minha personalidade para trabalhar melhor contigo.",
      calibNote: "(Podes mudar a qualquer momento com nexo_preference_set)",
      autonomyQ: "  Quanta autonomia me dás?\n    1. Conservador — pergunto antes de quase tudo\n    2. Equilibrado — ajo na rotina, pergunto no importante\n    3. Total — ajo primeiro, informo depois\n  > ",
      commQ: "\n  Como preferes que eu comunique?\n    1. Conciso — só resultados\n    2. Equilibrado — explicações breves quando úteis\n    3. Detalhado — raciocínio e trade-offs\n  > ",
      honestyQ: "\n  Quando não concordo:\n    1. Digo claramente\n    2. Menciono brevemente\n    3. Executo sem comentar\n  > ",
      proactiveQ: "\n  Quão proativo queres que eu seja?\n    1. Só o que pedes\n    2. Sugiro melhorias\n    3. Corrijo o que vejo e proponho otimizações\n  > ",
      errorQ: "\n  Quando erro:\n    1. Corrijo rápido\n    2. Explico o que correu mal\n  > ",
      scanQ: "  Queres que analise o teu ambiente para te conhecer a fundo?\n  Tudo fica local.\n\n    1. Sim, analisa tudo\n    2. Não, vou-te contando\n  > ",
      scanStart: "A conhecer-te... demora 1-2 minutos.",
      scanDone: "Pronto.",
      caffeinateQ: "  Ativar o helper de energia do Mac para processos em segundo plano?\n  (Usa caffeinate. Com a tampa fechada depende do teu setup; a recuperação ao despertar continua ativa.)\n    1. Sim\n    2. Não\n  > ",
      caffYes: "Helper de energia ativado.",
      caffNo: "Ok, a recuperação ao despertar cobrirá janelas perdidas.",
      dashboardQ: "  Ativar dashboard web em localhost:6174?\n  (UI sempre ativa para explorar memória, sessões e saúde do sistema)\n    1. Sim\n    2. Não\n  > ",
      dashYes: "Dashboard ativado.",
      dashNo: "Dashboard desativado. Iniciar manualmente: nexo dashboard",
      autoInstallQ: "  Posso instalar ferramentas automaticamente? (brew, pip, npm)\n    1. Sim\n    2. Pergunta antes\n  > ",
      autoInstallYes: "Auto-instalação ativada.",
      autoInstallNo: "Perguntarei antes.",
      installing: "A configurar...",
      ready: (name, alias) => `${name} está pronto. Abre um novo terminal e escreve: ${alias}`,
      readySubtext: "Na primeira vez que falarmos, termino de te conhecer\ncom umas perguntas que não consigo resolver sozinho.",
      profileTitle: "PERFIL",
    },
  };

  const existingIdentity = resolveExistingIdentityDefaults(NEXO_HOME);

  // Detect language from input or use default
  let lang = existingIdentity.language || "en";
  let t = i18n[lang] || i18n.en;
  if (!useDefaults) {
    const langInput = await ask("  What's your preferred language? / ¿En qué idioma prefieres hablar?\n  > ");
    const langLower = langInput.trim().toLowerCase();
    // Detect language from common responses
    if (/^(es|español|spanish|castellano)/.test(langLower)) lang = "es";
    else if (/^(fr|français|french|francais)/.test(langLower)) lang = "fr";
    else if (/^(de|deutsch|german|aleman)/.test(langLower)) lang = "de";
    else if (/^(it|italiano|italian)/.test(langLower)) lang = "it";
    else if (/^(pt|português|portuguese|portugues)/.test(langLower)) lang = "pt";
    else if (/^(en|english|inglés|ingles)/.test(langLower)) lang = "en";
    else {
      // Try to infer from the response language itself
      if (/[ñáéíóú]/.test(langLower) || /hola|sí|vale/.test(langLower)) lang = "es";
      else if (/[àâêîôû]|bonjour|oui/.test(langLower)) lang = "fr";
      else if (/[äöüß]|ja|hallo/.test(langLower)) lang = "de";
      else if (/ciao|sì|buon/.test(langLower)) lang = "it";
      else if (/[ãõ]|olá|sim/.test(langLower)) lang = "pt";
    }
    t = i18n[lang] || i18n.en;
    log(t.langConfirm);
    console.log("");
  }

  // Step 1b: Data directory
  if (!useDefaults) {
    const dataDirInput = await ask(t.askDataDir);
    const dataDirTrimmed = dataDirInput.trim();
    if (dataDirTrimmed) {
      // Expand ~ to home dir
      NEXO_HOME = dataDirTrimmed.replace(/^~/, require("os").homedir());
      // Resolve to absolute path
      NEXO_HOME = path.resolve(NEXO_HOME);
    }
    log(t.dataDirConfirm(NEXO_HOME));
    console.log("");
  }

  // Step 2: User's name (P2) — v6.0.0 empty input falls through to "Usuario"
  // instead of keeping an empty string. The calibration file always ships
  // with a concrete user.name so downstream tooling does not need guards.
  let userName = existingIdentity.userName || "Usuario";
  if (!useDefaults) {
    const nameInput = await ask(t.askUserName);
    const trimmedName = nameInput.trim();
    userName = trimmedName || "Usuario";
    if (trimmedName) {
      log(t.userGreet(trimmedName));
      console.log("");
    }
  }

  // Step 3: Agent name (P3)
  let operatorName = existingIdentity.operatorName || DEFAULT_ASSISTANT_NAME;
  if (!useDefaults) {
    while (true) {
      const name = await ask(t.askAgentName);
      const candidate = name.trim() || DEFAULT_ASSISTANT_NAME;
      if (!isReservedAssistantName(candidate)) {
        operatorName = candidate;
        break;
      }
      log(t.agentNameReserved);
      console.log("");
    }
  }
  log(t.agentConfirm(operatorName));
  console.log("");

  // Step 3b (v6.0.0): Resonance tier — the ONE power-level question. Drives
  // every runtime call for both Claude Code and Codex via resonance_tiers.json.
  let resonanceTier = DEFAULT_RESONANCE_TIER;
  if (!useDefaults) {
    resonanceTier = await askResonanceTier(lang, DEFAULT_RESONANCE_TIER);
    log(lang === "es"
      ? `Potencia por defecto: ${resonanceTier}.`
      : `Default power: ${resonanceTier}.`);
    console.log("");
  }

  // Step 4: Personality Calibration (P4-P8)
  let autonomyLevel = "full", communicationStyle = "concise", honestyLevel = "firm-pushback", proactivityLevel = "proactive", errorHandling = "brief-fix";

  if (!useDefaults) {
  log(t.calibTitle);
  log(t.calibNote);
  console.log("");

  const autonomyAnswer = await ask(t.autonomyQ);
  autonomyLevel = ["conservative", "balanced", "full"][parseInt(autonomyAnswer.trim()) - 1] || "balanced";

  const communicationAnswer = await ask(t.commQ);
  communicationStyle = ["concise", "balanced", "detailed"][parseInt(communicationAnswer.trim()) - 1] || "balanced";

  const honestyAnswer = await ask(t.honestyQ);
  honestyLevel = ["firm-pushback", "mention-and-follow", "just-execute"][parseInt(honestyAnswer.trim()) - 1] || "firm-pushback";

  const proactivityAnswer = await ask(t.proactiveQ);
  proactivityLevel = ["reactive", "suggestive", "proactive"][parseInt(proactivityAnswer.trim()) - 1] || "proactive";

  const errorAnswer = await ask(t.errorQ);
  errorHandling = ["brief-fix", "explain-and-learn"][parseInt(errorAnswer.trim()) - 1] || "brief-fix";
  } // end if (!useDefaults)

  console.log("");
  log(`Calibrated: autonomy=${autonomyLevel}, communication=${communicationStyle}, honesty=${honestyLevel}, proactivity=${proactivityLevel}, errors=${errorHandling}`);
  console.log("");

  // Save calibration (v6.0.0 — canonical nested shape with
  // preferences.default_resonance as the one knob for tier-only setup).
  const calibration = {
    version: 1,
    created: new Date().toISOString().slice(0, 10),
    user: {
      name: userName,
      language: lang,
      assistant_name: operatorName,
    },
    personality: {
      autonomy: autonomyLevel,
      communication: communicationStyle,
      honesty: honestyLevel,
      proactivity: proactivityLevel,
      error_handling: errorHandling,
    },
    preferences: {
      menu_on_demand: true,
      default_resonance: resonanceTier,
      report_style: "essentials_only",
      execution_first: true,
    },
    meta: {
      // v7.12.11 — only mark onboarding_completed when the user actually
      // answered the prompts (interactive run, !useDefaults). The
      // Desktop bootstrap calls nexo-brain with `--yes/--skip` to set up
      // the runtime non-interactively; in that path the values are
      // placeholders ("Usuario" / "en" / "Nova") and the real wizard
      // lives in the renderer. Marking it complete here used to short-
      // circuit that wizard and leave new users staring at an empty chat
      // (Inma 2026-05-03 smoke install).
      onboarding_completed: !useDefaults,
      onboarding_completed_at: !useDefaults ? new Date().toISOString() : null,
    },
    auto_install: "ask", // updated later if user answers P11
    calibrated_at: new Date().toISOString(),
  };
  const runtimeBrainDir = resolveRuntimeBrainDir(NEXO_HOME);

  // Step 5: Deep scan (P9) — v6.0.0 defaults flip to ON when running in
  // --yes/--skip mode; the interactive prompt below defaults to "yes" too
  // so a bare ENTER keeps the recommended setup.
  let doScan = useDefaults;
  let doCaffeinate = useDefaults && platform === "darwin";
  let doDashboard = useDefaults;
  let autoInstall = useDefaults ? "auto" : "ask";
  // v6.0.0 — bare ENTER on each of these prompts is interpreted as "yes"
  // because the recommended defaults are all on. An explicit "2" or "n"
  // turns the feature off.
  const answerIsYesDefault = (answer) => {
    const trimmed = String(answer || "").trim().toLowerCase();
    if (!trimmed) return true;
    if (trimmed === "1" || trimmed.startsWith("y") || trimmed.startsWith("s")) return true;
    return false;
  };
  if (!useDefaults) {
    const scanAnswer = await ask(t.scanQ);
    doScan = answerIsYesDefault(scanAnswer);
    console.log("");

    // Step 6: Caffeinate (P10) — macOS only
    if (platform === "darwin") {
      const caffeinateAnswer = await ask(t.caffeinateQ);
      doCaffeinate = answerIsYesDefault(caffeinateAnswer);
      log(doCaffeinate ? `✓ ${t.caffYes}` : t.caffNo);
      console.log("");
    }

    // Step 6b: Dashboard — always-on web UI
    const dashAnswer = await ask(t.dashboardQ);
    doDashboard = answerIsYesDefault(dashAnswer);
    log(doDashboard ? `✓ ${t.dashYes}` : t.dashNo);
    console.log("");

    // Step 7: Auto-install permission (P11)
    const autoInstallAnswer = await ask(t.autoInstallQ);
    autoInstall = answerIsYesDefault(autoInstallAnswer) ? "auto" : "ask";
    calibration.auto_install = autoInstall;
    log(`✓ ${autoInstall === "auto" ? t.autoInstallYes : t.autoInstallNo}`);
    console.log("");
  } else {
    log("Skipping interactive setup (non-interactive mode, defaults applied).");
    calibration.auto_install = autoInstall;
    console.log("");
  }

  // Commit calibration only after the wizard completed. This prevents Ctrl-C
  // from leaving placeholder defaults that look like real onboarding.
  fs.mkdirSync(NEXO_HOME, { recursive: true });
  writeJsonAtomic(path.join(runtimeBrainDir, "calibration.json"), calibration);

  if (smokeTestMode) {
    // Pytest fresh-install smoke only needs to prove that the non-interactive
    // onboarding path writes the current calibration shape. Skip the rest of
    // the heavy bootstrap (client installs, pip, scan, LaunchAgents) so the
    // smoke does not sit on long dependency timeouts inside sandboxes.
    log("Smoke test mode detected — wrote calibration and skipped heavy bootstrap.");
    closeReadline();
    return;
  }

  const clientConfig = await configureClientSetup({
    lang,
    useDefaults,
    autoInstall,
    detected: detectedClients,
  });
  const clientSetup = clientConfig.setup;
  detectedClients = clientConfig.detected;

  // Step 3: Install Python dependencies (use venv to avoid PEP 668 on modern Linux)
  log("Installing cognitive engine dependencies...");
  fs.mkdirSync(NEXO_HOME, { recursive: true });
  const venvPath = path.join(NEXO_HOME, ".venv");
  const venvPython = managedVenvPythonPath(NEXO_HOME);
  const bundledWheelsDir = path.join(__dirname, "..", "python-wheels");

  ensureManagedVenvCompatible(venvPath, venvPython);

  // Create venv if it doesn't exist
  if (!fs.existsSync(venvPython)) {
    log("  Creating Python virtual environment...");
    const venvResult = spawnSync(python, ["-m", "venv", venvPath], { stdio: "inherit" });
    if (venvResult.status !== 0) {
      if (platform === "linux" && fs.existsSync(bundledWheelsDir)) {
        log("  Python venv could not seed pip; retrying offline without pip...");
        try { fs.rmSync(venvPath, { recursive: true, force: true }); } catch {}
        const bareVenv = spawnSync(python, ["-m", "venv", "--without-pip", venvPath], { stdio: "inherit" });
        if (bareVenv.status !== 0) {
          log("Failed to create venv. Trying pip install directly...");
        }
      } else {
        log("Failed to create venv. Trying pip install directly...");
      }
    }
  }
  if (fs.existsSync(venvPython)) {
    const venvVersion = pythonVersion(venvPython);
    if (!venvVersion || !pythonVersionMeetsMinimum(venvVersion)) {
      log(`Python virtual environment is unsupported after creation (${venvVersion || "unknown version"}).`);
      process.exit(1);
    }
  }
  if (fs.existsSync(venvPython) && !pythonHasPip(venvPython)) {
    seedPipFromBundledWheels(venvPython, bundledWheelsDir);
  }

  // Use venv python if available, otherwise fall back to system python with --break-system-packages
  const pipPython = fs.existsSync(venvPython) ? venvPython : python;
  const requirementsFile = path.join(__dirname, "..", "src", "requirements.txt");
  // Detect bundled wheels in resources/python-wheels (offline-first). If
  // present, pip uses --no-index --find-links to install without internet.
  // Falls back to PyPI if bundle not found.
  // Desktop bundles Linux/WSL wheels and, from 0.32.44, macOS arm64/x64
  // wheels. Only use --no-index when the bundle clearly contains wheels
  // compatible with the current runtime; otherwise fall back to PyPI
  // instead of failing on ABI-mismatched wheels.
  const useBundle = bundledWheelsSupportCurrentPlatform(bundledWheelsDir);
  const pipArgs = useBundle
    ? ["-m", "pip", "install", "--no-index", "--find-links", bundledWheelsDir, "--progress-bar", "off", "-r", requirementsFile]
    : ["-m", "pip", "install", "-v", "--progress-bar", "off", "--default-timeout=60", "-r", requirementsFile];
  if (!fs.existsSync(venvPython)) {
    pipArgs.push("--break-system-packages");
  }
  if (useBundle) {
    log("  Installing Python deps from bundled wheels (offline)...");
  } else {
    log("  Installing Python deps from PyPI (online)...");
  }
  const pipInstall = spawnSync(pipPython, pipArgs, { stdio: "inherit" });
  if (pipInstall.status !== 0) {
    log("Failed to install Python dependencies.");
    log("Try manually: python3 -m venv ~/.nexo/.venv && ~/.nexo/.venv/bin/pip install -r src/requirements.txt");
    process.exit(1);
  }
  // Update python reference to use venv python for the rest of setup
  if (fs.existsSync(venvPython)) {
    python = venvPython;
  }
  log("Dependencies installed.");

  // OFFLINE-FIRST: copy bundled LLM models to runtime/models BEFORE warmup,
  // so fastembed finds them locally and skips the ~217MB HuggingFace download.
  // Bundle layout: resources/brain-bundle/models/<source-repo-name>/<all files>.
  // Target layout: <NEXO_HOME>/runtime/models/<spec.name slugified>/<revision>/<files>.
  // We map by source_repo basename to match local_model_manifest.json.
  const bundledModelsDir = path.join(__dirname, "..", "models");
  if (fs.existsSync(bundledModelsDir)) {
    try {
      const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "src", "local_model_manifest.json"), "utf8"));
      const runtimeModelsDir = path.join(NEXO_HOME, "runtime", "models");
      let modelsCopied = 0;
      for (const spec of manifest.models || []) {
        // Bundle layout supports either model_id basename (e.g.
        // "bge-base-en-v1.5" from "BAAI/bge-base-en-v1.5") or source_repo
        // basename (e.g. "bge-base-en-v1.5-onnx-q" from "qdrant/...").
        const modelIdName = (spec.model_id || "").split("/").pop();
        const sourceRepoName = (spec.source_repo || "").split("/").pop();
        let sourceDir = path.join(bundledModelsDir, modelIdName);
        if (!fs.existsSync(sourceDir)) {
          sourceDir = path.join(bundledModelsDir, sourceRepoName);
        }
        if (!fs.existsSync(sourceDir)) continue;
        const slug = (spec.name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        const targetDir = path.join(runtimeModelsDir, slug, spec.revision);
        fs.mkdirSync(targetDir, { recursive: true });
        for (const f of (spec.required_files || [])) {
          const src = path.join(sourceDir, f.path);
          const dst = path.join(targetDir, f.path);
          if (fs.existsSync(src) && !fs.existsSync(dst)) {
            fs.copyFileSync(src, dst);
          }
        }
        // Write the lock file to match revision (avoids re-download).
        fs.writeFileSync(path.join(targetDir, ".nexo-model-lock.json"), JSON.stringify({
          name: spec.name, kind: spec.kind, model_id: spec.model_id,
          source_repo: spec.source_repo, revision: spec.revision, model_file: spec.model_file,
          required_files: spec.required_files,
        }, null, 2));
        modelsCopied++;
      }
      if (modelsCopied > 0) log(`  Copied ${modelsCopied} pre-bundled LLM model(s) (offline).`);
    } catch (err) {
      log(`  WARN: bundled models copy failed: ${err.message}`);
    }
  }

  runDesktopAwareModelWarmup(python, NEXO_HOME, { reason: "install", installRuntimeDeps: false });

  // Step 4: Create ~/.nexo/
  log("Setting up NEXO home...");
  const dirs = [
    NEXO_HOME,
    path.join(NEXO_HOME, "bin"),
    path.join(NEXO_HOME, "core"),
    path.join(NEXO_HOME, "core", "plugins"),
    path.join(NEXO_HOME, "core", "scripts"),
    path.join(NEXO_HOME, "core", "skills"),
    path.join(NEXO_HOME, "core", "hooks"),
    path.join(NEXO_HOME, "core", "rules"),
    path.join(NEXO_HOME, "core", "dashboard"),
    path.join(NEXO_HOME, "personal"),
    path.join(NEXO_HOME, "personal", "brain"),
    path.join(NEXO_HOME, "personal", "config"),
    path.join(NEXO_HOME, "personal", "skills"),
    path.join(NEXO_HOME, "runtime"),
    path.join(NEXO_HOME, "runtime", "data"),
    path.join(NEXO_HOME, "runtime", "logs"),
    path.join(NEXO_HOME, "runtime", "backups"),
    path.join(NEXO_HOME, "runtime", "coordination"),
    path.join(NEXO_HOME, "runtime", "operations"),
    path.join(NEXO_HOME, "runtime", "crons"),
  ];
  dirs.forEach((d) => fs.mkdirSync(d, { recursive: true }));

  writeDesktopProductMode(NEXO_HOME);
  const evoObjectivePath = ensureEvolutionObjectiveForCurrentProductMode(NEXO_HOME);
  if (String(process.env.NEXO_DESKTOP_MANAGED || "").trim() === "1") {
    log("  Desktop product contract detected — evolution disabled.");
  } else if (fs.existsSync(evoObjectivePath)) {
    log("  Ensured evolution-objective.json in brain/");
  }

  // Write version file for auto-update tracking
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
  fs.writeFileSync(
    path.join(NEXO_HOME, "version.json"),
    JSON.stringify({
      version: pkg.version,
      installed_at: new Date().toISOString(),
      operator_name: operatorName,
      user_name: userName,
      language: lang,
      files_updated: 0,
    }, null, 2)
  );
  syncRuntimePackageMetadata(bundleRoot, NEXO_HOME);

  // Copy source files
  log("Copying core runtime files...");
  const srcDir = bundleSrcDir;
  const pluginsSrcDir = path.join(srcDir, "plugins");
  const scriptsSrcDir = path.join(srcDir, "scripts");
  const skillsSrcDir = path.join(srcDir, "skills");
  const templateDir = bundleTemplatesDir;

  // Recursive copy helper (skips __pycache__, .pyc, .db files)
  const copyDirRecursive = (src, dest) => {
    fs.mkdirSync(dest, { recursive: true });
    fs.readdirSync(src).forEach(item => {
      if (item === "__pycache__" || item.endsWith(".pyc") || item.endsWith(".db") || isDuplicateArtifactName(item, src)) return;
      const srcPath = path.join(src, item);
      const destPath = path.join(dest, item);
      if (fs.statSync(srcPath).isDirectory()) {
        copyDirRecursive(srcPath, destPath);
      } else {
        fs.copyFileSync(srcPath, destPath);
      }
    });
  };

  // Core flat files (single .py files in src/)
  const coreFiles = getCoreRuntimeFlatFiles(srcDir);
  coreFiles.forEach((f) => {
    const src = path.join(srcDir, f);
    if (fs.existsSync(src)) {
      const dest = path.join(NEXO_HOME, "core", f);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(src, dest);
    }
  });

  // Publish Brain contracts to ~/.nexo/brain/ (read by NEXO Desktop et al.)
  publishBrainContracts(srcDir, NEXO_HOME);

  // Runtime CLI wrapper lives in NEXO_HOME/bin so it survives npx installs.
  const runtimeCli = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    "",
    `RUNTIME_HOME="${NEXO_HOME}"`,
    'NEXO_HOME="$RUNTIME_HOME"',
    'export NEXO_HOME',
    'export PYTHONDONTWRITEBYTECODE=1',
    'resolve_code_dir() {',
    '  if [ -n "${NEXO_CODE:-}" ] && [ -f "${NEXO_CODE%/}/cli.py" ]; then',
    '    printf \'%s\\n\' "${NEXO_CODE%/}"',
    '    return 0',
    '  fi',
    '  if [ -f "$NEXO_HOME/core/current/cli.py" ]; then',
    '    printf \'%s\\n\' "$NEXO_HOME/core/current"',
    '    return 0',
    '  fi',
    '  if [ -f "$NEXO_HOME/core/cli.py" ]; then',
    '    printf \'%s\\n\' "$NEXO_HOME/core"',
    '    return 0',
    '  fi',
    '  if [ -f "$NEXO_HOME/cli.py" ]; then',
    '    printf \'%s\\n\' "$NEXO_HOME"',
    '    return 0',
    '  fi',
    '  if [ -d "$NEXO_HOME/core" ]; then',
    '    printf \'%s\\n\' "$NEXO_HOME/core"',
    '    return 0',
    '  fi',
    '  printf \'%s\\n\' "$NEXO_HOME"',
    '}',
    'NEXO_CODE="$(resolve_code_dir)"',
    'export NEXO_CODE',
    'resolve_python() {',
    '  local candidates=()',
    '  local candidate=""',
    '  if [ -n "${NEXO_RUNTIME_PYTHON:-}" ]; then candidates+=("$NEXO_RUNTIME_PYTHON"); fi',
    '  if [ -n "${NEXO_PYTHON:-}" ]; then candidates+=("$NEXO_PYTHON"); fi',
    '  candidates+=("$NEXO_CODE/.venv/bin/python3" "$NEXO_CODE/.venv/bin/python")',
    '  if [ "$NEXO_CODE" != "$NEXO_HOME" ]; then',
    '    candidates+=("$NEXO_HOME/.venv/bin/python3" "$NEXO_HOME/.venv/bin/python")',
    '  fi',
    '  case "$(uname -s)" in',
    '    Darwin) candidates+=("/opt/homebrew/bin/python3" "/usr/local/bin/python3") ;;',
    '    *) candidates+=("/usr/local/bin/python3" "/usr/bin/python3") ;;',
    '  esac',
    '  if command -v python3 >/dev/null 2>&1; then candidates+=("$(command -v python3)"); fi',
    '  if command -v python >/dev/null 2>&1; then candidates+=("$(command -v python)"); fi',
    '  for candidate in "${candidates[@]}"; do',
    '    [ -n "$candidate" ] || continue',
    '    [ -x "$candidate" ] || continue',
    '    if NEXO_HOME="$NEXO_HOME" NEXO_CODE="$NEXO_CODE" "$candidate" -c "import fastmcp" >/dev/null 2>&1; then',
    '      printf \'%s\\n\' "$candidate"',
    '      return 0',
    '    fi',
    '  done',
    '  for candidate in "${candidates[@]}"; do',
    '    [ -n "$candidate" ] || continue',
    '    [ -x "$candidate" ] || continue',
    '    printf \'%s\\n\' "$candidate"',
    '    return 0',
    '  done',
    '  return 1',
    '}',
    'PYTHON="$(resolve_python || true)"',
    'if [ -z "$PYTHON" ]; then',
    '  echo "NEXO runtime Python not found. Run nexo-brain or nexo update to repair the installation." >&2',
    '  exit 1',
    'fi',
    'read_runtime_version() {',
    '  local base="${1:-}"',
    '  [ -n "$base" ] || return 0',
    '  local candidate=""',
    '  for candidate in "$base/version.json" "$base/package.json"; do',
    '    [ -f "$candidate" ] || continue',
    '    "$PYTHON" -c "import json, sys; from pathlib import Path; payload=json.loads(Path(sys.argv[1]).read_text(encoding=\\"utf-8\\")); version=str(payload.get(\\"version\\", \\"\\")).strip(); sys.stdout.write(version); sys.exit(0 if version else 1)" "$candidate" 2>/dev/null || continue',
    '    return 0',
    '  done',
    '  return 0',
    '}',
    'repair_stale_current_runtime() {',
    '  local core_root="$NEXO_HOME/core"',
    '  local current_root="$NEXO_HOME/core/current"',
    '  [ -d "$core_root" ] || return 0',
    '  [ -e "$current_root" ] || return 0',
    '  local core_version=""',
    '  local current_version=""',
    '  core_version="$(read_runtime_version "$core_root")"',
    '  current_version="$(read_runtime_version "$current_root")"',
    '  [ -n "$core_version" ] || return 0',
    '  [ "$core_version" = "$current_version" ] && return 0',
    '  NEXO_HOME="$NEXO_HOME" NEXO_CODE="$core_root" "$PYTHON" -c "import os, sys; from pathlib import Path; home=Path(os.environ[\\"NEXO_HOME\\"]); core=home / \\"core\\"; sys.path.insert(0, str(core)); from runtime_versioning import activate_versioned_runtime_snapshot, read_version_for_path; version=read_version_for_path(core); result=activate_versioned_runtime_snapshot(source_root=core, version=str(version or \\"\\").strip()); sys.exit(0 if result.get(\\"ok\\") else 1)" >/dev/null 2>&1 || return 0',
    '}',
    'repair_stale_current_runtime',
    'CLI_PY="$NEXO_CODE/cli.py"',
    'if [ ! -f "$CLI_PY" ] && [ -f "$NEXO_HOME/core/current/cli.py" ]; then',
    '  NEXO_CODE="$NEXO_HOME/core/current"',
    '  export NEXO_CODE',
    '  CLI_PY="$NEXO_HOME/core/current/cli.py"',
    'fi',
    'if [ ! -f "$CLI_PY" ] && [ -f "$NEXO_HOME/core/cli.py" ]; then',
    '  NEXO_CODE="$NEXO_HOME/core"',
    '  export NEXO_CODE',
    '  CLI_PY="$NEXO_HOME/core/cli.py"',
    'fi',
    'if [ ! -f "$CLI_PY" ] && [ -f "$NEXO_HOME/cli.py" ]; then',
    '  NEXO_CODE="$NEXO_HOME"',
    '  export NEXO_CODE',
    '  CLI_PY="$NEXO_HOME/cli.py"',
    'fi',
    'if [ ! -f "$CLI_PY" ]; then',
    '  echo "NEXO CLI not found under $NEXO_HOME. Run nexo-brain or nexo update to repair the installation." >&2',
    '  exit 1',
    'fi',
    'exec "$PYTHON" "$CLI_PY" "$@"',
    "",
  ].join("\n");
  const runtimeCliPath = path.join(NEXO_HOME, "bin", "nexo");
  fs.writeFileSync(runtimeCliPath, runtimeCli);
  fs.chmodSync(runtimeCliPath, 0o755);

  log("Copying core packages...");
  // Core packages (directories with __init__.py)
  getCoreRuntimePackages().forEach(pkg => {
    const pkgSrc = path.join(srcDir, pkg);
    if (fs.existsSync(pkgSrc)) {
      copyDirRecursive(pkgSrc, path.join(NEXO_HOME, "core", pkg));
    }
  });

  log("Copying plugins, scripts, and templates...");
  // Plugins (all .py files in plugins/)
  fs.mkdirSync(path.join(NEXO_HOME, "core", "plugins"), { recursive: true });
  if (fs.existsSync(pluginsSrcDir)) {
    fs.readdirSync(pluginsSrcDir).filter(f => f.endsWith(".py") && !isDuplicateArtifactName(f, pluginsSrcDir)).forEach((f) => {
      fs.copyFileSync(path.join(pluginsSrcDir, f), path.join(NEXO_HOME, "core", "plugins", f));
    });
  }

  // Scripts (all files + subdirectories like deep-sleep/)
  if (fs.existsSync(scriptsSrcDir)) {
    copyDirRecursive(scriptsSrcDir, path.join(NEXO_HOME, "core", "scripts"));
    // Make .sh files executable
    const scriptsDest = path.join(NEXO_HOME, "core", "scripts");
    fs.readdirSync(scriptsDest).filter(f => f.endsWith(".sh")).forEach(f => {
      fs.chmodSync(path.join(scriptsDest, f), "755");
    });
    syncWatchdogHashRegistry(NEXO_HOME);
  }

  // Core skills are shipped separately from personal skills.
  if (fs.existsSync(skillsSrcDir)) {
    copyDirRecursive(skillsSrcDir, path.join(NEXO_HOME, "core", "skills"));
    log("  Core skills installed.");
  }

  // Dashboard (recursive — includes static/, templates/)
  const dashSrcDir = path.join(srcDir, "dashboard");
  if (fs.existsSync(dashSrcDir)) {
    copyDirRecursive(dashSrcDir, path.join(NEXO_HOME, "core", "dashboard"));
    log("  Dashboard installed.");
  }

  // Rules directory
  const rulesSrcDir = path.join(srcDir, "rules");
  if (fs.existsSync(rulesSrcDir)) {
    copyDirRecursive(rulesSrcDir, path.join(NEXO_HOME, "core", "rules"));
    log("  Rules installed.");
  }

  // Crons directory (manifest.json + sync.py — needed by catchup & watchdog)
  const cronsSrcDir = path.join(srcDir, "crons");
  if (fs.existsSync(cronsSrcDir)) {
    copyDirRecursive(cronsSrcDir, path.join(NEXO_HOME, "runtime", "crons"));
    log("  Crons installed.");
  }

  // Templates directory (scripts + skills scaffolds)
  const templatesDest = path.join(NEXO_HOME, "templates");
  fs.mkdirSync(templatesDest, { recursive: true });
  if (fs.existsSync(templateDir)) {
    // Copy all template files (not just a hardcoded subset)
    for (const f of fs.readdirSync(templateDir)) {
      const src = path.join(templateDir, f);
      const dest = path.join(templatesDest, f);
      if (fs.statSync(src).isFile()) {
        fs.copyFileSync(src, dest);
      } else if (fs.statSync(src).isDirectory()) {
        fs.mkdirSync(dest, { recursive: true });
        for (const sf of fs.readdirSync(src)) {
          const ssrc = path.join(src, sf);
          if (fs.statSync(ssrc).isFile()) {
            fs.copyFileSync(ssrc, path.join(dest, sf));
          }
        }
      }
    }
    log("  All templates installed.");
  }

  // Hooks directory
  const hooksSrcDir = path.join(srcDir, "hooks");
  if (fs.existsSync(hooksSrcDir)) {
    const hooksDest = path.join(NEXO_HOME, "core", "hooks");
    copyDirRecursive(hooksSrcDir, hooksDest);
    // Make .sh files executable
    fs.readdirSync(hooksDest).filter(f => f.endsWith(".sh")).forEach(f => {
      fs.chmodSync(path.join(hooksDest, f), "755");
    });
    log("  Hooks installed.");
  }
  writeRuntimeCoreArtifactsManifest(NEXO_HOME, srcDir);

  // Generate personality
  const personality = `# ${operatorName} — Personality

I am ${operatorName}, a cognitive co-operator. Not an assistant — an operational partner.

## Core traits
- Direct: I say what I think, not what sounds nice
- Action-oriented: I do things, I don't suggest things
- Self-critical: I track my mistakes and learn from them
- Proactive: If I can detect or fix something without being asked, I do it

## What I never do
- Ask the user to do something I can do myself
- Say "I can't" without trying alternatives first
- Give long explanations when a short answer suffices
- Repeat mistakes I've already logged
`;
  fs.writeFileSync(path.join(resolveRuntimeBrainDir(NEXO_HOME), "personality.md"), personality);

  // Deep scan (P9) — comprehensive environment analysis
  const profileData = {
    scanned_at: new Date().toISOString(),
    user_name: userName,
    language: lang,
    operator_name: operatorName,
    system: {},
    code: {},
    apps: [],
    git: {},
    ssh: [],
    terminal: {},
    browser: {},
    email: [],
    calendar: {},
    contacts: [],
    documents: {},
    notes: { count: 0, folders: [] },
    reminders: { count: 0, lists: [] },
    photos: { count: 0 },
    messaging: [],
    interests: [],
    summary: {},
  };

  if (doScan) {
    log(t.scanStart);
    console.log("");
    const home = require("os").homedir();

    // --- System info ---
    process.stdout.write("  \u280B System...\r");
    profileData.system.platform = platform;
    profileData.system.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    profileData.system.locale = Intl.DateTimeFormat().resolvedOptions().locale || lang;
    profileData.system.hostname = require("os").hostname();
    const darkMode = platform === "darwin" ? run("defaults read -g AppleInterfaceStyle 2>/dev/null") : null;
    profileData.system.dark_mode = darkMode === "Dark";
    const kbLayout = platform === "darwin" ? run("defaults read com.apple.HIToolbox AppleCurrentKeyboardLayoutInputSourceID 2>/dev/null") : null;
    if (kbLayout) profileData.system.keyboard = kbLayout.split(".").pop();
    log(`\u2713 System: ${profileData.system.timezone}, ${profileData.system.dark_mode ? "dark mode" : "light mode"}${profileData.system.keyboard ? `, keyboard ${profileData.system.keyboard}` : ""}`);

    // --- Code projects ---
    process.stdout.write("  \u280B Code projects...\r");
    const repos = [];
    const langCounts = {};
    // Search common project locations
    const projectDirs = ["Documents", "Projects", "projects", "src", "code", "Code", "dev", "Dev", "repos", "workspace", "Workspace", "Desktop"];
    const dirsToScan = projectDirs
      .map(d => path.join(home, d))
      .filter(d => fs.existsSync(d));
    dirsToScan.push(home); // also scan home root (depth 1)

    for (const dir of dirsToScan) {
      const maxDepth = dir === home ? 1 : 4;
      const gitFind = run(`find "${dir}" -maxdepth ${maxDepth} -name ".git" -type d 2>/dev/null`);
      if (gitFind) {
        for (const gitDir of gitFind.split("\n").filter(Boolean)) {
          const repoPath = path.dirname(gitDir);
          if (repos.some(r => r.path === repoPath)) continue;
          const repoName = path.basename(repoPath);
          // Detect languages by file extensions
          const files = run(`find "${repoPath}" -maxdepth 2 -type f \\( -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.tsx" -o -name "*.jsx" -o -name "*.go" -o -name "*.rs" -o -name "*.java" -o -name "*.php" -o -name "*.rb" -o -name "*.swift" -o -name "*.kt" \\) 2>/dev/null | head -100`);
          const exts = {};
          if (files) {
            files.split("\n").filter(Boolean).forEach(f => {
              const ext = path.extname(f).slice(1);
              const langMap = { py: "Python", js: "JavaScript", ts: "TypeScript", tsx: "TypeScript", jsx: "JavaScript", go: "Go", rs: "Rust", java: "Java", php: "PHP", rb: "Ruby", swift: "Swift", kt: "Kotlin" };
              const l = langMap[ext] || ext;
              exts[l] = (exts[l] || 0) + 1;
              langCounts[l] = (langCounts[l] || 0) + 1;
            });
          }
          const mainLang = Object.keys(exts).sort((a, b) => exts[b] - exts[a])[0] || "unknown";
          // Check last commit date
          const lastCommit = run(`git -C "${repoPath}" log -1 --format=%ci 2>/dev/null`);
          const isRecent = lastCommit && (Date.now() - new Date(lastCommit).getTime()) < 30 * 24 * 60 * 60 * 1000; // 30 days
          repos.push({ name: repoName, path: repoPath, language: mainLang, recent: isRecent, last_commit: lastCommit ? lastCommit.split(" ")[0] : null });
        }
      }
    }
    profileData.code.repos = repos;
    profileData.code.total = repos.length;
    profileData.code.active_last_month = repos.filter(r => r.recent).length;
    // Calculate language percentages
    const totalFiles = Object.values(langCounts).reduce((a, b) => a + b, 0);
    profileData.code.languages = {};
    if (totalFiles > 0) {
      Object.keys(langCounts).sort((a, b) => langCounts[b] - langCounts[a]).forEach(l => {
        profileData.code.languages[l] = Math.round(langCounts[l] / totalFiles * 100);
      });
    }
    const langSummary = Object.entries(profileData.code.languages).slice(0, 3).map(([l, p]) => `${l} ${p}%`).join(", ");
    log(`\u2713 ${repos.length} repositories (${profileData.code.active_last_month} active last month)${langSummary ? ` — ${langSummary}` : ""}`);

    // --- Installed apps ---
    process.stdout.write("  \u280B Apps...\r");
    if (platform === "darwin") {
      const appsRaw = run("ls /Applications 2>/dev/null");
      if (appsRaw) {
        profileData.apps = appsRaw.split("\n")
          .filter(a => a.endsWith(".app"))
          .map(a => a.replace(".app", ""));
      }
    } else {
      // Linux: check common commands
      const linuxApps = ["code", "docker", "figma", "slack", "spotify", "firefox", "chromium", "vim", "nvim", "emacs", "postman", "insomnia", "gimp", "inkscape", "obs", "telegram-desktop"];
      profileData.apps = linuxApps.filter(a => run(`which ${a} 2>/dev/null`));
    }
    log(`\u2713 ${profileData.apps.length} apps detected`);

    // --- Git config ---
    process.stdout.write("  \u280B Git identity...\r");
    profileData.git.name = run("git config --global user.name 2>/dev/null") || "";
    profileData.git.email = run("git config --global user.email 2>/dev/null") || "";
    const gitAliases = run("git config --global --get-regexp alias 2>/dev/null");
    profileData.git.aliases = gitAliases ? gitAliases.split("\n").filter(Boolean).length : 0;
    // Total commits last year across all repos
    let totalCommits = 0;
    for (const repo of repos.slice(0, 20)) { // limit to 20 to keep it fast
      const count = run(`git -C "${repo.path}" rev-list --count --since="1 year ago" HEAD 2>/dev/null`);
      if (count) totalCommits += parseInt(count) || 0;
    }
    profileData.git.commits_last_year = totalCommits;
    if (profileData.git.name) {
      log(`\u2713 Git: ${profileData.git.name} <${profileData.git.email}> — ${totalCommits} commits/year${profileData.git.aliases ? `, ${profileData.git.aliases} aliases` : ""}`);
    }

    // --- SSH connections ---
    process.stdout.write("  \u280B SSH connections...\r");
    const sshConfig = path.join(home, ".ssh", "config");
    if (fs.existsSync(sshConfig)) {
      try {
        const sshContent = fs.readFileSync(sshConfig, "utf8");
        const hosts = sshContent.match(/^Host\s+(\S+)/gm);
        if (hosts) {
          profileData.ssh = hosts
            .map(h => h.replace(/^Host\s+/, ""))
            .filter(h => h !== "*" && !h.includes("*"));
        }
      } catch {}
    }
    if (profileData.ssh.length > 0) {
      log(`\u2713 ${profileData.ssh.length} SSH connections in ~/.ssh/config`);
    }

    // --- Terminal history ---
    process.stdout.write("  \u280B Terminal patterns...\r");
    const histFile = fs.existsSync(path.join(home, ".zsh_history"))
      ? path.join(home, ".zsh_history")
      : path.join(home, ".bash_history");
    if (fs.existsSync(histFile)) {
      try {
        const histRaw = fs.readFileSync(histFile, "utf8");
        const lines = histRaw.split("\n").filter(Boolean);
        profileData.terminal.total_commands = lines.length;
        // Extract command patterns (first word of each line)
        const cmdCounts = {};
        lines.forEach(line => {
          // zsh history format: : timestamp:0;command or just command
          const cmd = line.replace(/^:\s*\d+:\d+;/, "").trim().split(/\s+/)[0];
          if (cmd && cmd.length > 1 && !cmd.startsWith("#")) {
            cmdCounts[cmd] = (cmdCounts[cmd] || 0) + 1;
          }
        });
        profileData.terminal.top_commands = Object.entries(cmdCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 20)
          .map(([cmd, count]) => ({ cmd, count }));

        // Detect work patterns from zsh history timestamps
        if (histFile.includes("zsh")) {
          const hourCounts = new Array(24).fill(0);
          const dayCounts = new Array(7).fill(0);
          lines.forEach(line => {
            const match = line.match(/^:\s*(\d+):/);
            if (match) {
              const date = new Date(parseInt(match[1]) * 1000);
              hourCounts[date.getHours()]++;
              dayCounts[date.getDay()]++;
            }
          });
          // Find peak hours
          const peakHours = hourCounts
            .map((c, h) => ({ h, c }))
            .sort((a, b) => b.c - a.c)
            .slice(0, 6)
            .map(x => x.h)
            .sort((a, b) => a - b);
          profileData.terminal.peak_hours = peakHours;
          // Find peak days
          const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
          const peakDays = dayCounts
            .map((c, d) => ({ d: dayNames[d], c }))
            .sort((a, b) => b.c - a.c)
            .slice(0, 3)
            .map(x => x.d);
          profileData.terminal.peak_days = peakDays;
        }
      } catch {}
    }
    if (profileData.terminal.total_commands) {
      const peakInfo = profileData.terminal.peak_hours
        ? ` — peak hours: ${profileData.terminal.peak_hours.map(h => `${h}:00`).join(", ")}`
        : "";
      log(`\u2713 ${profileData.terminal.total_commands.toLocaleString()} commands analyzed${peakInfo}`);
    }

    // --- Browser bookmarks ---
    process.stdout.write("  \u280B Browser data...\r");
    // Chrome bookmarks
    const chromeBookmarks = platform === "darwin"
      ? path.join(home, "Library", "Application Support", "Google", "Chrome", "Default", "Bookmarks")
      : path.join(home, ".config", "google-chrome", "Default", "Bookmarks");
    if (fs.existsSync(chromeBookmarks)) {
      try {
        const bm = JSON.parse(fs.readFileSync(chromeBookmarks, "utf8"));
        const countBookmarks = (node) => {
          let count = 0;
          if (node.type === "url") count++;
          if (node.children) node.children.forEach(c => count += countBookmarks(c));
          return count;
        };
        let total = 0;
        const categories = [];
        if (bm.roots) {
          Object.values(bm.roots).forEach(root => {
            total += countBookmarks(root);
            if (root.children) {
              root.children.forEach(c => {
                if (c.type === "folder" && c.name) categories.push(c.name);
              });
            }
          });
        }
        profileData.browser.bookmarks_count = total;
        profileData.browser.bookmark_categories = categories.slice(0, 10);
      } catch {}
    }
    // Chrome extensions
    const chromeExtDir = platform === "darwin"
      ? path.join(home, "Library", "Application Support", "Google", "Chrome", "Default", "Extensions")
      : path.join(home, ".config", "google-chrome", "Default", "Extensions");
    if (fs.existsSync(chromeExtDir)) {
      try {
        const extDirs = fs.readdirSync(chromeExtDir).filter(d => d.length === 32);
        profileData.browser.extensions_count = extDirs.length;
      } catch {}
    }
    if (profileData.browser.bookmarks_count) {
      log(`\u2713 Browser: ${profileData.browser.bookmarks_count} bookmarks${profileData.browser.extensions_count ? `, ${profileData.browser.extensions_count} extensions` : ""}`);
    }

    // --- Email accounts ---
    process.stdout.write("  \u280B Email accounts...\r");
    if (platform === "darwin") {
      // macOS Mail.app accounts — try multiple detection methods
      // Method 1: sandboxed container (modern macOS)
      let mailAccounts = run("defaults read ~/Library/Containers/com.apple.mail/Data/Library/Preferences/com.apple.mail MailAccounts 2>/dev/null | grep -E 'AccountName|EmailAddresses' | head -30");
      // Method 2: legacy plist (older macOS)
      if (!mailAccounts) mailAccounts = run("defaults read com.apple.mail MailAccounts 2>/dev/null | grep -E 'AccountName|EmailAddresses' | head -30");
      // Method 3: Internet Accounts (covers Mail, Calendar, Contacts, etc.)
      if (!mailAccounts) mailAccounts = run("defaults read com.apple.internetaccounts Accounts 2>/dev/null | grep -E 'AccountDescription|Username' | head -30");
      // Method 4: scan Mail directory for account folders
      if (!mailAccounts) {
        const mailDir = run("ls -1 ~/Library/Mail/V*/  2>/dev/null | grep -v '^$' | head -20");
        if (mailDir) mailAccounts = mailDir;
      }
      if (mailAccounts) {
        profileData.email = mailAccounts.split("\n")
          .map(l => l.replace(/.*=\s*"?/, "").replace(/"?\s*;?\s*$/, "").replace(/\/$/, "").trim())
          .filter(l => l && l.length > 1 && !l.startsWith("(") && !l.startsWith(")") && !l.includes("{") && !l.includes("}"))
          .filter((v, i, a) => a.indexOf(v) === i); // dedupe
      }
    }
    if (profileData.email.length > 0) {
      log(`\u2713 ${profileData.email.length} email accounts detected`);
    }

    // --- Calendar (macOS) ---
    process.stdout.write("  \u280B Calendar...\r");
    if (platform === "darwin") {
      const calDir = path.join(home, "Library", "Calendars");
      if (fs.existsSync(calDir)) {
        // Count calendar sources
        const calSources = run(`find "${calDir}" -maxdepth 2 -name "Info.plist" 2>/dev/null | wc -l`);
        profileData.calendar.sources = parseInt((calSources || "0").trim());
        // Try to get recent events count
        const eventsCount = run(`find "${calDir}" -name "*.ics" 2>/dev/null | wc -l`);
        profileData.calendar.events = parseInt((eventsCount || "0").trim());
      }
    }
    if (profileData.calendar.events) {
      log(`\u2713 Calendar: ${profileData.calendar.events} events across ${profileData.calendar.sources || "?"} calendars`);
    }

    // --- Contacts (macOS) ---
    process.stdout.write("  \u280B Contacts...\r");
    if (platform === "darwin") {
      const contactsDir = path.join(home, "Library", "Application Support", "AddressBook", "Sources");
      if (fs.existsSync(contactsDir)) {
        const vcfCount = run(`find "${contactsDir}" -name "*.abcdp" 2>/dev/null | wc -l`);
        profileData.contacts = { count: parseInt((vcfCount || "0").trim()) };
      }
    }
    if (profileData.contacts.count) {
      log(`\u2713 ${profileData.contacts.count} contacts indexed`);
    }

    // --- Recent documents ---
    process.stdout.write("  \u280B Documents...\r");
    const docDirs = ["Documents", "Desktop", "Downloads"].map(d => path.join(home, d));
    const docExts = [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".pptx"];
    let totalDocs = 0;
    const docTypes = {};
    for (const dir of docDirs) {
      if (fs.existsSync(dir)) {
        const findCmd = docExts.map(e => `-name "*${e}"`).join(" -o ");
        const docs = run(`find "${dir}" -maxdepth 2 -type f \\( ${findCmd} \\) -mtime -90 2>/dev/null | head -200`);
        if (docs) {
          docs.split("\n").filter(Boolean).forEach(f => {
            const ext = path.extname(f).slice(1);
            docTypes[ext] = (docTypes[ext] || 0) + 1;
            totalDocs++;
          });
        }
      }
    }
    profileData.documents.recent_count = totalDocs;
    profileData.documents.types = docTypes;
    if (totalDocs > 0) {
      const typesSummary = Object.entries(docTypes).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([t, c]) => `${c} ${t}`).join(", ");
      log(`\u2713 ${totalDocs} recent documents (${typesSummary})`);
    }

    // --- Notes ---
    process.stdout.write("  \u280B Notes...\r");
    profileData.notes = { count: 0, folders: [] };
    if (platform === "linux") {
      // GNOME Notes / Tomboy / Obsidian vaults
      const obsidianVaults = run(`find "${home}" -maxdepth 3 -name ".obsidian" -type d 2>/dev/null | head -5`);
      if (obsidianVaults) {
        const vaults = obsidianVaults.split("\n").filter(Boolean);
        let totalNotes = 0;
        for (const v of vaults) {
          const vaultDir = path.dirname(v);
          const count = run(`find "${vaultDir}" -name "*.md" -type f 2>/dev/null | wc -l`);
          totalNotes += parseInt((count || "0").trim());
        }
        profileData.notes.count = totalNotes;
        profileData.notes.folders = vaults.map(v => path.basename(path.dirname(v)));
      }
    } else if (platform === "darwin") {
      const notesDb = path.join(home, "Library", "Group Containers", "group.com.apple.notes", "NoteStore.sqlite");
      if (fs.existsSync(notesDb)) {
        const noteCount = run(`sqlite3 "${notesDb}" "SELECT COUNT(*) FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE IS NOT NULL AND ZMARKEDFORDELETION != 1" 2>/dev/null`);
        profileData.notes.count = parseInt((noteCount || "0").trim());
        const folders = run(`sqlite3 "${notesDb}" "SELECT DISTINCT ZTITLE2 FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE2 IS NOT NULL AND ZMARKEDFORDELETION != 1 LIMIT 15" 2>/dev/null`);
        if (folders) profileData.notes.folders = folders.split("\n").filter(Boolean);
      }
    }
    if (profileData.notes.count > 0) {
      log(`\u2713 ${profileData.notes.count} notes${profileData.notes.folders.length ? ` in ${profileData.notes.folders.length} folders` : ""}`);
    }

    // --- Reminders ---
    process.stdout.write("  \u280B Reminders...\r");
    profileData.reminders = { count: 0, lists: [] };
    if (platform === "linux") {
      // GNOME Reminders / Todoist / task files
      const todoFiles = run(`find "${home}" -maxdepth 2 -name "todo.txt" -o -name "TODO.md" -o -name "tasks.md" 2>/dev/null | head -5`);
      if (todoFiles) {
        let count = 0;
        todoFiles.split("\n").filter(Boolean).forEach(f => {
          const lines = run(`wc -l < "${f}" 2>/dev/null`);
          count += parseInt((lines || "0").trim());
        });
        profileData.reminders.count = count;
        profileData.reminders.lists = todoFiles.split("\n").filter(Boolean).map(f => path.basename(f));
      }
    } else if (platform === "darwin") {
      // Reminders uses EventKit, but we can count via the Calendars directory or osascript
      const reminderCount = run('osascript -e \'tell application "Reminders" to count of (every reminder whose completed is false)\' 2>/dev/null');
      if (reminderCount) profileData.reminders.count = parseInt(reminderCount.trim()) || 0;
      const reminderLists = run('osascript -e \'tell application "Reminders" to get name of every list\' 2>/dev/null');
      if (reminderLists) profileData.reminders.lists = reminderLists.split(", ").filter(Boolean);
    }
    if (profileData.reminders.count > 0) {
      log(`\u2713 ${profileData.reminders.count} active reminders across ${profileData.reminders.lists.length} lists`);
    }

    // --- Photos library size (macOS) ---
    process.stdout.write("  \u280B Photos...\r");
    profileData.photos = { count: 0 };
    if (platform === "darwin") {
      const photosDb = path.join(home, "Pictures", "Photos Library.photoslibrary", "database", "Photos.sqlite");
      if (fs.existsSync(photosDb)) {
        const photoCount = run(`sqlite3 "${photosDb}" "SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0" 2>/dev/null`);
        if (photoCount) profileData.photos.count = parseInt(photoCount.trim()) || 0;
      }
    }
    if (profileData.photos.count > 0) {
      log(`\u2713 ${profileData.photos.count.toLocaleString()} photos in library`);
    }

    // --- Messaging apps ---
    process.stdout.write("  \u280B Messaging...\r");
    const msgApps = { "WhatsApp": "WhatsApp.app", "Telegram": "Telegram.app", "Slack": "Slack.app", "Discord": "Discord.app", "Signal": "Signal.app", "Teams": "Microsoft Teams.app", "Zoom": "zoom.us.app" };
    if (platform === "darwin") {
      profileData.messaging = Object.entries(msgApps)
        .filter(([_, app]) => fs.existsSync(path.join("/Applications", app)))
        .map(([name]) => name);
    }
    if (profileData.messaging.length > 0) {
      log(`\u2713 Messaging: ${profileData.messaging.join(", ")}`);
    }

    // --- Build summary ---
    process.stdout.write("  \u280B Building profile...\r");
    const topLangs = Object.keys(profileData.code.languages || {}).slice(0, 3);
    const topApps = profileData.apps
      .filter(a => !["Utilities", "System Preferences", "App Store", "Calculator", "Preview", "TextEdit", "Font Book", "Chess", "Stickies"].includes(a))
      .slice(0, 10);

    profileData.summary = {
      primary_stack: topLangs.join(", ") || "not detected",
      repos: repos.length,
      servers: profileData.ssh.length,
      email_accounts: profileData.email.length,
      notes: profileData.notes.count,
      reminders: profileData.reminders.count,
      photos: profileData.photos.count,
      recent_documents: profileData.documents.recent_count || 0,
      contacts: profileData.contacts.count || 0,
      key_tools: topApps.slice(0, 8),
      work_hours: profileData.terminal.peak_hours || [],
      peak_days: profileData.terminal.peak_days || [],
    };

    log(`\u2713 ${t.scanDone}`);
    console.log("");

    // Display profile summary
    const pad = (s, len) => s + " ".repeat(Math.max(0, len - s.length));
    const boxW = 60;
    const line = (text) => console.log(`  \u2551  ${pad(text, boxW - 5)}\u2551`);
    console.log(`  \u2554${"═".repeat(boxW - 2)}\u2557`);
    line(`${t.profileTitle}: ${userName || profileData.git.name || "User"}`);
    line("");
    if (topLangs.length) line(`Stack: ${topLangs.join(", ")}`);
    // Life data
    const lifeParts = [];
    if (profileData.email.length) lifeParts.push(`${profileData.email.length} email`);
    if (profileData.notes.count) lifeParts.push(`${profileData.notes.count} notes`);
    if (profileData.reminders.count) lifeParts.push(`${profileData.reminders.count} reminders`);
    if (profileData.contacts.count) lifeParts.push(`${profileData.contacts.count} contacts`);
    if (profileData.photos.count) lifeParts.push(`${profileData.photos.count.toLocaleString()} photos`);
    if (profileData.documents.recent_count) lifeParts.push(`${profileData.documents.recent_count} docs`);
    if (lifeParts.length) line(lifeParts.join(" \u00B7 "));
    // Dev data
    if (repos.length || profileData.ssh.length) {
      line(`${repos.length} repos \u00B7 ${profileData.ssh.length} servers`);
    }
    if (topLangs.length) line(`Stack: ${topLangs.join(", ")}`);
    if (topApps.length) line(`Tools: ${topApps.slice(0, 6).join(", ")}`);
    if (totalCommits) line(`${totalCommits.toLocaleString()} commits/year`);
    if (profileData.terminal.peak_hours) {
      const hours = profileData.terminal.peak_hours;
      const ranges = [];
      let start = hours[0], prev = hours[0];
      for (let i = 1; i <= hours.length; i++) {
        if (i < hours.length && hours[i] === prev + 1) { prev = hours[i]; continue; }
        ranges.push(start === prev ? `${start}:00` : `${start}-${prev + 1}h`);
        if (i < hours.length) { start = hours[i]; prev = hours[i]; }
      }
      line(`Hours: ${ranges.join(", ")}${profileData.terminal.peak_days ? ` \u00B7 Peak: ${profileData.terminal.peak_days.join(", ")}` : ""}`);
    }
    if (profileData.messaging.length) line(`Messaging: ${profileData.messaging.join(", ")}`);
    if (profileData.calendar.events) line(`Calendar: ${profileData.calendar.events} events`);
    line(`Timezone: ${profileData.system.timezone}`);
    line("");
    line(lang === "es" ? "Ya te conozco. Vamos a trabajar." : lang === "fr" ? "Je te connais. Au travail." : lang === "de" ? "Ich kenne dich. Los geht's." : lang === "it" ? "Ti conosco. Al lavoro." : lang === "pt" ? "J\u00E1 te conhe\u00E7o. Ao trabalho." : "I know you now. Let's work.");
    console.log(`  \u255A${"═".repeat(boxW - 2)}\u255D`);
    console.log("");

    // Save full profile
    fs.writeFileSync(
      path.join(resolveRuntimeBrainDir(NEXO_HOME), "profile.json"),
      JSON.stringify(profileData, null, 2)
    );
    log(`Saved to ${path.join(resolveRuntimeBrainDir(NEXO_HOME), "profile.json")}`);

  } else {
    // No scan — save minimal profile
    fs.writeFileSync(
      path.join(resolveRuntimeBrainDir(NEXO_HOME), "profile.json"),
      JSON.stringify(profileData, null, 2)
    );
    log(lang === "es" ? "Sin problema. Iré aprendiéndote sobre la marcha." : "No problem. I'll learn about you as we go.");
  }

  // Generate user profile markdown (from scan or minimal)
  const profileMd = `# User Profile

Name: ${userName || "Unknown"}
Created: ${new Date().toISOString().split("T")[0]}
Operator: ${operatorName}
Language: ${lang}

## Detected (from deep scan)
${doScan ? `- Stack: ${Object.keys(profileData.code.languages || {}).slice(0, 5).join(", ") || "none"}
- Repos: ${profileData.code.repos ? profileData.code.repos.length : 0}
- Servers: ${profileData.ssh ? profileData.ssh.length : 0}
- Email accounts: ${profileData.email ? profileData.email.length : 0}
- Work hours: ${profileData.terminal.peak_hours ? profileData.terminal.peak_hours.map(h => h + ":00").join(", ") : "unknown"}` : "(scan skipped — ${operatorName} will learn over time)"}

## Observed preferences
(${operatorName} will learn these over time)

## Work patterns
(${operatorName} will observe and record these)
`;
  fs.writeFileSync(path.join(resolveRuntimeBrainDir(NEXO_HOME), "user-profile.md"), profileMd);

  console.log("");

  // Step 6: Configure Claude Code MCP
  log("Configuring Claude Code MCP server...");
  let settings = {};
  if (fs.existsSync(CLAUDE_SETTINGS)) {
    try {
      settings = JSON.parse(fs.readFileSync(CLAUDE_SETTINGS, "utf8"));
    } catch {
      settings = {};
    }
  }

  if (!settings.mcpServers) settings.mcpServers = {};
  settings.mcpServers.nexo = {
    command: python,
    args: [runtimeServerPath(NEXO_HOME)],
    env: {
      NEXO_HOME: NEXO_HOME,
      NEXO_CODE: runtimeCodeDir(NEXO_HOME),
      NEXO_NAME: operatorName,
    },
  };

  // Configure ALL 8 core hooks for session capture (Sensory Register)
  if (!settings.hooks) settings.hooks = {};

  // Hook scripts already copied above — just reference the dest dir
  const hooksDestDir = runtimeHooksDir(NEXO_HOME);

  registerAllCoreHooks(settings, hooksDestDir, NEXO_HOME);

  const settingsDir = path.dirname(CLAUDE_SETTINGS);
  fs.mkdirSync(settingsDir, { recursive: true });
  fs.writeFileSync(CLAUDE_SETTINGS, JSON.stringify(settings, null, 2));
  log("MCP server + 8 core hooks configured in Claude Code settings.");

  const syncClientsScript = path.join(runtimeScriptsDir(NEXO_HOME), "nexo-sync-clients.py");
  if (fs.existsSync(syncClientsScript)) {
    const syncArgs = [
      syncClientsScript,
      "--nexo-home", NEXO_HOME,
      "--runtime-root", runtimeCodeDir(NEXO_HOME),
      "--python", python,
      "--operator-name", operatorName,
    ];
    const enabledSyncClients = Array.from(new Set([
      ...Object.entries(clientSetup.interactive_clients)
        .filter(([, enabled]) => Boolean(enabled))
        .map(([key]) => key),
      ...(clientSetup.automation_enabled && clientSetup.automation_backend !== "none"
        ? [clientSetup.automation_backend]
        : []),
    ]));
    enabledSyncClients.forEach((client) => {
      syncArgs.push("--enabled-client", client);
    });
    syncArgs.push("--json");
    const syncResult = spawnSync(
      python,
      syncArgs,
      { encoding: "utf8" }
    );
    if (syncResult.status === 0) {
      try {
        const payload = JSON.parse(syncResult.stdout || "{}");
        const clients = payload.clients || {};
        const fmt = (name, key) => {
          const item = clients[key] || {};
          if (item.skipped) return `${name}: skipped`;
          if (item.ok) return `${name}: synced`;
          return `${name}: warning`;
        };
        log(`Shared brain client sync complete (${fmt("Claude Code", "claude_code")}, ${fmt("Claude Desktop", "claude_desktop")}, ${fmt("Codex", "codex")}).`);
      } catch {
        log("Shared brain client sync complete.");
      }
    } else {
      const errMsg = (syncResult.stderr || syncResult.stdout || "").trim();
      log(`WARN: shared brain client sync failed: ${errMsg || "unknown error"}`);
    }
  }

  const claudeCliPath = detectInstalledClients().claude_code.path || run("which claude", { env: buildManagedCliEnv() }) || run("which claude") || "";
  if (claudeCliPath) {
    persistClaudeCliPath(claudeCliPath.trim());
    log(`Claude CLI path saved: ${claudeCliPath.trim()}`);
  }

  // Step 7: Create schedule.json (only on fresh install) and install core processes
  log("Setting up automated processes...");
  let schedule = loadOrCreateSchedule(NEXO_HOME);
  schedule = applyClientSetupToSchedule(schedule, clientSetup);
  fs.writeFileSync(resolveRuntimeSchedulePath(NEXO_HOME), JSON.stringify(schedule, null, 2));
  schedule = await maybeConfigurePowerPolicy(schedule, useDefaults);
  schedule = await maybeConfigurePublicContribution(schedule, useDefaults);
  schedule = await maybeConfigureFullDiskAccess(schedule, useDefaults, python);
  const enabledOptionals = { dashboard: doDashboard, automation: schedule.automation_enabled !== false };

  // Persist optional process preferences before cron sync so the manifest
  // installer reads the same automation/dashboard state we just computed.
  try {
    const configDir = resolveRuntimeConfigDir(NEXO_HOME);
    fs.mkdirSync(configDir, { recursive: true });
    const optFile = path.join(configDir, "optionals.json");
    fs.writeFileSync(optFile, JSON.stringify(enabledOptionals, null, 2));
  } catch {}

  if (smokeTestMode) {
    log("Smoke test mode detected — skipping LaunchAgents installation.");
  } else if (isEphemeralInstall(NEXO_HOME)) {
    log("Ephemeral HOME/NEXO_HOME detected — skipping LaunchAgents installation.");
  } else {
    const cronSync = syncCoreProcessesFromManifest(python, NEXO_HOME, path.join(__dirname, "..", "src", "crons"));
    if (cronSync.ok) {
      log("Core crons reconciled with manifest.");
    } else {
      log(`Cron sync warning: ${cronSync.error}. Falling back to legacy installer.`);
      installAllProcesses(platform, python, NEXO_HOME, schedule, LAUNCH_AGENTS, enabledOptionals);
      log("Automated processes configured via legacy installer fallback.");
    }
  }

  // Manifest-driven cron sync now owns the steady-state install path.
  // The legacy installer remains only as a bootstrap fallback.

  // Step 7b: macOS Keychain setup for headless automation
  await setupKeychainPassFile(NEXO_HOME);

  // Step 8: Create shell alias and add runtime CLI to PATH
  const aliasName = operatorName.toLowerCase();
  const installSkipShell = shouldSkipShellProfileBackfill();
  if (installSkipShell.skip) {
    log(`Skipping shell profile setup — ${installSkipShell.reason}`);
    log(`(Runtime CLI wrapper still installed at ${path.join(NEXO_HOME, "bin", "nexo")}; add it to PATH manually if needed.)`);
    console.log("");
  } else {
  const nexoPathLine = `export PATH="${path.join(NEXO_HOME, "bin")}:$PATH"`;
  const nexoPathComment = "# NEXO runtime CLI";

  // Detect shell rc files
  const userShell = process.env.SHELL || "/bin/bash";
  const homeDir = require("os").homedir();
  const rcFiles = [];

  if (userShell.includes("zsh")) {
    rcFiles.push(path.join(homeDir, ".zshrc"));
  } else {
    // Bash: always write to .bash_profile (macOS login shells)
    rcFiles.push(path.join(homeDir, ".bash_profile"));
    // Also write to .bashrc (Linux interactive shells) — create if needed
    const bashrc = path.join(homeDir, ".bashrc");
    rcFiles.push(bashrc);
  }

  // Skip alias when operator name matches the CLI binary ("nexo") to avoid shadowing it
  const skipAlias = aliasName === "nexo";
  const aliasLine = skipAlias ? null : `alias ${aliasName}='nexo chat .'`;
  const aliasComment = skipAlias ? null : `# ${operatorName} — open the configured NEXO terminal client`;

  for (const rcFile of rcFiles) {
    let rcContent = "";
    if (fs.existsSync(rcFile)) {
      rcContent = fs.readFileSync(rcFile, "utf8");
    }

    if (!rcContent.includes(nexoPathLine)) {
      fs.appendFileSync(rcFile, `\n${nexoPathComment}\n${nexoPathLine}\n`);
      log(`Added NEXO runtime CLI to ${path.basename(rcFile)}`);
      rcContent += `\n${nexoPathComment}\n${nexoPathLine}\n`;
    } else {
      log(`Runtime CLI already present in ${path.basename(rcFile)}`);
    }

    if (!skipAlias) {
      if (!rcContent.includes(`alias ${aliasName}=`)) {
        fs.appendFileSync(rcFile, `\n${aliasComment}\n${aliasLine}\n`);
        log(`Added '${aliasName}' alias to ${path.basename(rcFile)}`);
      } else {
        log(`Alias '${aliasName}' already exists in ${path.basename(rcFile)}`);
      }
    }
  }
  if (skipAlias) {
    log(`Operator name is 'nexo' — skipping alias (CLI binary already provides 'nexo' command)`);
  } else {
    log(`After setup, open a new terminal and type: ${aliasName} or nexo`);
  }
  console.log("");
  }

  // Step 9: Generate CLAUDE.md template
  log("Generating operator instructions...");
  const templateSrc = path.join(templateDir, "CLAUDE.md.template");
  let claudeMd = "";
  if (fs.existsSync(templateSrc)) {
    claudeMd = fs
      .readFileSync(templateSrc, "utf8")
      .replace(/\{\{NAME\}\}/g, operatorName)
      .replace(/\{\{NEXO_HOME\}\}/g, NEXO_HOME);
  } else {
    claudeMd = `# ${operatorName} — Cognitive Co-Operator

Instructions for ${operatorName} are generated during setup.
See ~/.nexo/ for configuration.
`;
  }

  // Write to user's global CLAUDE.md if it doesn't exist
  const userClaudeMd = path.join(require("os").homedir(), ".claude", "CLAUDE.md");
  if (!fs.existsSync(userClaudeMd)) {
    fs.writeFileSync(userClaudeMd, claudeMd);
    log("Created ~/.claude/CLAUDE.md with operator instructions.");
  } else {
    // Save as reference
    fs.writeFileSync(path.join(NEXO_HOME, "CLAUDE.md.generated"), claudeMd);
    log(
      "~/.claude/CLAUDE.md already exists. Generated template saved to ~/.nexo/CLAUDE.md.generated"
    );
  }

  // Write initial CLAUDE.md version tracker
  const claudeMdVersionMatch = claudeMd.match(/nexo-claude-md-version:\s*([\d.]+)/);
  if (claudeMdVersionMatch) {
    const dataDir = resolveRuntimeDataDir(NEXO_HOME);
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(path.join(dataDir, "claude_md_version.txt"), claudeMdVersionMatch[1]);
    log(`CLAUDE.md version tracker initialized: v${claudeMdVersionMatch[1]}`);
  }

  log("Finalizing F0.6 runtime layout...");
  const layoutFinalize = finalizeF06Layout(python, NEXO_HOME);
  if (!layoutFinalize.ok) {
    throw new Error(`F0.6 layout finalization failed: ${layoutFinalize.error}`);
  }

  console.log("");
  const readyMsg = t.ready(operatorName, aliasName);
  const readySub = t.readySubtext;
  const bw = 60;
  const padR = (s, len) => s + " ".repeat(Math.max(0, len - s.length));
  console.log(`  \u2554${"═".repeat(bw - 2)}\u2557`);
  console.log(`  \u2551  ${padR("", bw - 5)}\u2551`);
  console.log(`  \u2551  ${padR(readyMsg, bw - 5)}\u2551`);
  console.log(`  \u2551  ${padR("", bw - 5)}\u2551`);
  readySub.split("\n").forEach(l => {
    console.log(`  \u2551  ${padR(l, bw - 5)}\u2551`);
  });
  console.log(`  \u2551  ${padR("", bw - 5)}\u2551`);
  console.log(`  \u255A${"═".repeat(bw - 2)}\u255D`);
  console.log("");

  if (_stagedRuntimeCleanup) {
    _stagedRuntimeCleanup();
    _stagedRuntimeCleanup = null;
  }
  closeReadline();
}

async function main() {
  const handled = await maybeHandleTopLevelCommand();
  if (!handled) {
    await runSetup();
  }
}

main().catch((err) => {
  closeReadline();
  console.error("Setup failed:", err.message);
  process.exit(1);
});
