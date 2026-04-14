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
const path = require("path");
const readline = require("readline");

let NEXO_HOME = process.env.NEXO_HOME || path.join(require("os").homedir(), ".nexo");
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

function isEphemeralInstall(nexoHome) {
  const homeDir = require("os").homedir();
  const allowEphemeral = process.env.NEXO_ALLOW_EPHEMERAL_INSTALL === "1";
  if (allowEphemeral) return false;
  return nexoHome.startsWith("/tmp/") || homeDir.startsWith("/tmp/");
}

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
});

function ask(question) {
  return new Promise((resolve) => rl.question(question, resolve));
}

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, { encoding: "utf8", stdio: "pipe", ...opts }).trim();
  } catch {
    return null;
  }
}

function log(msg) {
  console.log(`  ${msg}`);
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
    const watchdogPath = path.join(nexoHome, "scripts", "nexo-watchdog.sh");
    if (!fs.existsSync(watchdogPath)) return;

    const registryPath = path.join(nexoHome, "scripts", ".watchdog-hashes");
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
    const configDir = path.join(nexoHome, "config");
    fs.mkdirSync(configDir, { recursive: true });
    const payload = {
      generated_at: new Date().toISOString(),
      script_names: listTopLevelFiles(path.join(srcDir, "scripts")),
      hook_names: listTopLevelFiles(path.join(srcDir, "hooks")),
    };
    fs.writeFileSync(
      path.join(configDir, "runtime-core-artifacts.json"),
      `${JSON.stringify(payload, null, 2)}\n`
    );
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
        // Include Python modules and any flat JSON config the Python runtime
        // reads at import time (e.g. model_defaults.json). The "_defaults.json"
        // suffix convention lets us add future config JSONs without touching
        // this list.
        return name.endsWith(".py") || name.endsWith("_defaults.json");
      })
    : [];
  return [...new Set([...staticFiles, ...discoveredRootModules])];
}

function getCoreRuntimePackages() {
  return ["db", "cognitive", "doctor"];
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
  const configDir = path.join(nexoHome, "config");
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

  const logsDir = path.join(nexoHome, "logs");
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
    fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  if (current === "granted") {
    const probe = probeFullDiskAccess(NEXO_HOME);
    if (probe.granted) {
      fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
      return schedule;
    }
    schedule.full_disk_access_status = "later";
  } else if (current === "declined") {
    fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  if (useDefaults || !process.stdin.isTTY || !process.stdout.isTTY) {
    schedule.full_disk_access_status = current === "granted" ? "later" : current || "unset";
    fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
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

  fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
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
const ALL_CORE_HOOKS = [
  { event: "SessionStart", key: "session-start-ts", commandTemplate: (nexoHome) =>
      `date +%s > ${path.join(nexoHome, "operations", ".session-start-ts")}`,
    timeout: 2, purpose: "Session timing" },
  { event: "SessionStart", key: "daily-briefing-check.sh", script: "daily-briefing-check.sh",
    timeout: 5, purpose: "Briefing schedule check" },
  { event: "SessionStart", key: "session-start.sh", script: "session-start.sh",
    timeout: 35, purpose: "Briefing + context" },
  { event: "Stop", key: "session-stop.sh", script: "session-stop.sh",
    timeout: 10, purpose: "POSTMORTEM — the most important" },
  { event: "PostToolUse", key: "capture-tool-logs.sh", script: "capture-tool-logs.sh",
    timeout: 5, purpose: "Operation capture" },
  { event: "PostToolUse", key: "capture-session.sh", script: "capture-session.sh",
    timeout: 3, purpose: "Sensory register (session_buffer.jsonl)" },
  { event: "PostToolUse", key: "inbox-hook.sh", script: "inbox-hook.sh",
    timeout: 5, purpose: "Inter-session messaging" },
  { event: "PreCompact", key: "pre-compact.sh", script: "pre-compact.sh",
    timeout: 10, purpose: "Memory preservation" },
  { event: "PostCompact", key: "post-compact.sh", script: "post-compact.sh",
    timeout: 10, purpose: "Memory restoration" },
];

/**
 * Register all 8 core hooks in settings.hooks.
 * Additive + auto-migrate: adds missing hooks, updates stale paths, never removes user's custom ones.
 */
function registerAllCoreHooks(settings, hooksDir, nexoHome) {
  if (!settings.hooks) settings.hooks = {};

  // Ensure operations dir exists for timestamp file
  const opsDir = path.join(nexoHome, "operations");
  fs.mkdirSync(opsDir, { recursive: true });

  for (const hook of ALL_CORE_HOOKS) {
    if (!settings.hooks[hook.event]) settings.hooks[hook.event] = [];

    // Build the canonical command for this hook
    let command;
    if (hook.commandTemplate) {
      command = hook.commandTemplate(nexoHome);
    } else {
      command = `NEXO_HOME=${nexoHome} bash ${path.join(hooksDir, hook.script)}`;
    }

    // Claude Code settings.hooks supports two formats:
    //   Flat:   [{type:"command", command:"..."}]
    //   Nested: [{matcher:"*", hooks:[{type:"command", command:"..."}]}]
    // We need to search and update in both formats.
    let found = false;

    for (let idx = 0; idx < settings.hooks[hook.event].length; idx++) {
      const entry = settings.hooks[hook.event][idx];
      if (entry.hooks && Array.isArray(entry.hooks)) {
        // Nested format: {matcher, hooks: [...]}
        if (!entry.matcher) entry.matcher = "*";
        const subIdx = entry.hooks.findIndex(
          (h) => h.command && h.command.includes(hook.key)
        );
        if (subIdx !== -1) {
          const existing = entry.hooks[subIdx];
          if (existing.command !== command) existing.command = command;
          if (hook.timeout && !existing.timeout) existing.timeout = hook.timeout;
          found = true;
          break;
        }
      } else if (entry.command && entry.command.includes(hook.key)) {
        // Legacy flat format: migrate to nested matcher+hooks.
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
  }
}

/**
 * Load schedule.json if it exists, or create it with defaults on fresh install.
 * NEVER overwrites an existing schedule.json (user customization).
 */
function loadOrCreateSchedule(nexoHome) {
  const configDir = path.join(nexoHome, "config");
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
    client_runtime_profiles: {
      claude_code: {
        model: DEFAULT_CLAUDE_CODE_MODEL,
        reasoning_effort: DEFAULT_CLAUDE_CODE_REASONING_EFFORT,
      },
      codex: {
        model: DEFAULT_CODEX_MODEL,
        reasoning_effort: DEFAULT_CODEX_REASONING_EFFORT,
      },
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
  const claudeBin = run("which claude") || "";
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
  return {
    claude_code: {
      model: DEFAULT_CLAUDE_CODE_MODEL,
      reasoning_effort: DEFAULT_CLAUDE_CODE_REASONING_EFFORT,
    },
    codex: {
      model: DEFAULT_CODEX_MODEL,
      reasoning_effort: DEFAULT_CODEX_REASONING_EFFORT,
    },
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

function runtimeProfileCatalog(lang, client) {
  const recommended = lang === "es" ? " (recomendado)" : " (recommended)";
  if (client === "claude_code") {
    return {
      modelQuestion: `  ¿Qué modelo debe usar ${runtimeClientLabel(client)} para chat y background cuando sea el cliente/backend activo?`,
      modelQuestionEn: `  Which model should ${runtimeClientLabel(client)} use for chat and background when it is the active client/backend?`,
      effortQuestion: `  ¿Qué nivel de esfuerzo debe usar ${runtimeClientLabel(client)}?`,
      effortQuestionEn: `  Which effort level should ${runtimeClientLabel(client)} use?`,
      customModelQuestion: `  Escribe el alias/nombre de modelo para ${runtimeClientLabel(client)} > `,
      customModelQuestionEn: `  Enter the model alias/name for ${runtimeClientLabel(client)} > `,
      customEffortQuestion: `  Escribe el effort para ${runtimeClientLabel(client)} (vacío = default) > `,
      customEffortQuestionEn: `  Enter the effort for ${runtimeClientLabel(client)} (blank = default) > `,
      modelDefault: DEFAULT_CLAUDE_CODE_MODEL,
      effortDefault: "",
      modelOptions: [
        { value: DEFAULT_CLAUDE_CODE_MODEL, label: `Opus 4.6 with 1M context${recommended}` },
        { value: "claude-opus-4-6", label: "Opus 4.6" },
        { value: "sonnet", label: "Sonnet latest" },
        { value: "custom", label: lang === "es" ? "Modelo personalizado" : "Custom model" },
      ],
      effortOptions: [
        { value: "", label: lang === "es" ? `Effort por defecto${recommended}` : `Default effort${recommended}` },
        { value: "high", label: "high" },
        { value: "max", label: "max" },
        { value: "custom", label: lang === "es" ? "Effort personalizado" : "Custom effort" },
      ],
    };
  }

  return {
    modelQuestion: `  ¿Qué modelo debe usar ${runtimeClientLabel(client)} para chat y background cuando sea el cliente/backend activo?`,
    modelQuestionEn: `  Which model should ${runtimeClientLabel(client)} use for chat and background when it is the active client/backend?`,
    effortQuestion: `  ¿Qué razonamiento debe usar ${runtimeClientLabel(client)}?`,
    effortQuestionEn: `  Which reasoning effort should ${runtimeClientLabel(client)} use?`,
    customModelQuestion: `  Escribe el nombre del modelo para ${runtimeClientLabel(client)} > `,
    customModelQuestionEn: `  Enter the model name for ${runtimeClientLabel(client)} > `,
    customEffortQuestion: `  Escribe el reasoning effort para ${runtimeClientLabel(client)} > `,
    customEffortQuestionEn: `  Enter the reasoning effort for ${runtimeClientLabel(client)} > `,
    modelDefault: DEFAULT_CODEX_MODEL,
    effortDefault: DEFAULT_CODEX_REASONING_EFFORT,
    modelOptions: [
      { value: "gpt-5.4", label: `GPT-5.4${recommended}` },
      { value: "gpt-5.4-pro", label: "GPT-5.4 Pro" },
      { value: "gpt-5.4-mini", label: "GPT-5.4 mini" },
      { value: "custom", label: lang === "es" ? "Modelo personalizado" : "Custom model" },
    ],
    effortOptions: [
      { value: "xhigh", label: `xhigh${recommended}` },
      { value: "high", label: "high" },
      { value: "medium", label: "medium" },
      { value: "low", label: "low" },
      { value: "none", label: "none" },
      { value: "custom", label: lang === "es" ? "Effort personalizado" : "Custom effort" },
    ],
  };
}

async function askClientRuntimeProfile({ lang, client, currentProfile }) {
  const catalog = runtimeProfileCatalog(lang, client);
  const modelQuestion = lang === "es" ? catalog.modelQuestion : catalog.modelQuestionEn;
  const effortQuestion = lang === "es" ? catalog.effortQuestion : catalog.effortQuestionEn;
  const customModelQuestion = lang === "es" ? catalog.customModelQuestion : catalog.customModelQuestionEn;
  const customEffortQuestion = lang === "es" ? catalog.customEffortQuestion : catalog.customEffortQuestionEn;
  let model = await askChoice(modelQuestion, catalog.modelOptions, currentProfile.model || catalog.modelDefault);
  if (model === "custom") {
    model = (await ask(customModelQuestion)).trim() || catalog.modelDefault;
  }
  let reasoningEffort = await askChoice(
    effortQuestion,
    catalog.effortOptions,
    currentProfile.reasoning_effort ?? catalog.effortDefault,
  );
  if (reasoningEffort === "custom") {
    reasoningEffort = (await ask(customEffortQuestion)).trim();
  }
  return {
    model,
    reasoning_effort: reasoningEffort,
  };
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
  let claudeInstalled = run("which claude");
  if (claudeInstalled) return { installed: true, path: claudeInstalled };

  spawnSync("npx", ["-y", "@anthropic-ai/claude-code", "--version"], { stdio: "pipe", timeout: 60000 });
  claudeInstalled = run("which claude");
  if (!claudeInstalled) {
    const npmCmd = platform === "linux" ? "sudo" : "npm";
    const npmArgs = platform === "linux" ? ["npm", "install", "-g", "@anthropic-ai/claude-code"] : ["install", "-g", "@anthropic-ai/claude-code"];
    spawnSync(npmCmd, npmArgs, { stdio: "inherit" });
    claudeInstalled = run("which claude");
  }
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
    const label = setup.automation_backend === "claude_code" ? "Claude Code" : "Codex";
    log(strings.automationDisabled(label));
    setup.automation_enabled = false;
    setup.automation_backend = "none";
  }

  if (!detected.claude_desktop.installed && setup.interactive_clients.claude_desktop) {
    log(strings.desktopManual);
  }

  if (!useDefaults) {
    const activeRuntimeClients = Array.from(new Set([
      setup.default_terminal_client,
      ...(setup.automation_enabled && setup.automation_backend !== "none" ? [setup.automation_backend] : []),
    ].filter(Boolean)));
    for (const client of activeRuntimeClients) {
      setup.client_runtime_profiles[client] = await askClientRuntimeProfile({
        lang,
        client,
        currentProfile: setup.client_runtime_profiles[client] || defaultClientRuntimeProfiles()[client] || {},
      });
    }
  }

  const defaultProfile = formatRuntimeProfile(
    setup.client_runtime_profiles[setup.default_terminal_client] || defaultClientRuntimeProfiles()[setup.default_terminal_client] || {}
  );
  const backendProfile = setup.automation_enabled && setup.automation_backend !== "none"
    ? formatRuntimeProfile(
      setup.client_runtime_profiles[setup.automation_backend] || defaultClientRuntimeProfiles()[setup.automation_backend] || {}
    )
    : "";
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
  fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
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
    fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
    return schedule;
  }

  if (useDefaults || !process.stdin.isTTY || !process.stdout.isTTY) {
    schedule.public_contribution = current;
    fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
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
  fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
  return schedule;
}

/**
 * Resolve the venv python path for an existing NEXO_HOME installation.
 */
function findVenvPython(nexoHome) {
  const venvPy = path.join(nexoHome, ".venv", "bin", "python3");
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
  const logsDir = path.join(nexoHome, "logs");
  fs.mkdirSync(logsDir, { recursive: true });

  // Resolve script path: "root" means NEXO_HOME directly, "scripts" means NEXO_HOME/scripts/
  function scriptPath(proc) {
    const dir = proc.scriptDir === "root" ? nexoHome : path.join(nexoHome, "scripts");
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

async function main() {
  // Non-interactive mode: --defaults or --yes skips all prompts
  const useDefaults = process.argv.includes("--defaults") || process.argv.includes("--yes") || process.argv.includes("-y");

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
    log("Windows detected. NEXO Brain requires WSL (Windows Subsystem for Linux).");
    log("Install WSL: https://learn.microsoft.com/en-us/windows/wsl/install");
    log("Then run this command inside WSL (Ubuntu terminal), not PowerShell/CMD.");
    process.exit(1);
  }
  if (platform !== "darwin" && platform !== "linux") {
    log(`Unsupported platform: ${platform}. NEXO supports macOS and Linux (Windows via WSL).`);
    process.exit(1);
  }

  // Auto-migration: detect existing installation
  const versionFile = path.join(NEXO_HOME, "version.json");
  if (fs.existsSync(versionFile)) {
    try {
      const installed = JSON.parse(fs.readFileSync(versionFile, "utf8"));
      const currentPkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
      const installedVersion = installed.version || "0.0.0";
      const currentVersion = currentPkg.version;

      if (installedVersion !== currentVersion) {
        log(`Existing installation detected: v${installedVersion} → v${currentVersion}`);
        log("Running auto-migration...");

        // Recursive copy helper (skips __pycache__, .pyc, .db files)
        const srcDir = path.join(__dirname, "..", "src");
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
        const hooksDest = path.join(NEXO_HOME, "hooks");
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
            fs.copyFileSync(src, path.join(NEXO_HOME, f));
          }
        });
        // Update core packages (db/, cognitive/) — full directory copy
        getCoreRuntimePackages().forEach(pkg => {
          const pkgSrc = path.join(srcDir, pkg);
          if (fs.existsSync(pkgSrc)) {
            copyDirRec(pkgSrc, path.join(NEXO_HOME, pkg));
          }
        });
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

        // Update plugins (all .py files in plugins/)
        const pluginsSrc = path.join(srcDir, "plugins");
        const pluginsDest = path.join(NEXO_HOME, "plugins");
        fs.mkdirSync(pluginsDest, { recursive: true });
        if (fs.existsSync(pluginsSrc)) {
          fs.readdirSync(pluginsSrc).filter(f => f.endsWith(".py") && !isDuplicateArtifactName(f, pluginsSrc)).forEach((f) => {
            fs.copyFileSync(path.join(pluginsSrc, f), path.join(pluginsDest, f));
          });
        }
        log("  Plugins updated.");

        // Update dashboard (recursive — includes static/, templates/)
        const dashSrc = path.join(srcDir, "dashboard");
        const dashDest = path.join(NEXO_HOME, "dashboard");
        if (fs.existsSync(dashSrc)) {
          copyDirRec(dashSrc, dashDest);
          log("  Dashboard updated.");
        }

        // Update rules (directory with core-rules.json, __init__.py, migrate.py)
        const rulesSrc = path.join(srcDir, "rules");
        const rulesDest = path.join(NEXO_HOME, "rules");
        if (fs.existsSync(rulesSrc)) {
          copyDirRec(rulesSrc, rulesDest);
          log("  Rules updated.");
        }

        // Update crons (manifest.json + sync.py — needed by catchup & watchdog)
        const cronsMigSrc = path.join(srcDir, "crons");
        const cronsMigDest = path.join(NEXO_HOME, "crons");
        if (fs.existsSync(cronsMigSrc)) {
          copyDirRec(cronsMigSrc, cronsMigDest);
          log("  Crons updated.");
        }

        // Update scripts (all .py, .sh files + subdirectories like deep-sleep/)
        const scriptsSrc = path.join(srcDir, "scripts");
        const scriptsDest = path.join(NEXO_HOME, "scripts");
        if (fs.existsSync(scriptsSrc)) {
          copyDirRec(scriptsSrc, scriptsDest);
          // Make .sh files executable
          fs.readdirSync(scriptsDest).filter(f => f.endsWith(".sh")).forEach(f => {
            fs.chmodSync(path.join(scriptsDest, f), "755");
          });
        }
        writeRuntimeCoreArtifactsManifest(NEXO_HOME, srcDir);
        log("  Scripts updated.");

        // Register ALL 8 core hooks in settings.json (additive — don't remove user's custom hooks)
        let settings = {};
        if (fs.existsSync(CLAUDE_SETTINGS)) {
          try { settings = JSON.parse(fs.readFileSync(CLAUDE_SETTINGS, "utf8")); } catch {}
        }
        if (!settings.hooks) settings.hooks = {};
        const migHooksDest = path.join(NEXO_HOME, "hooks");
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
          const optFile = path.join(NEXO_HOME, "config", "optionals.json");
          if (fs.existsSync(optFile)) migOptionals = JSON.parse(fs.readFileSync(optFile, "utf8"));
        } catch {}
        installAllProcesses(platform, migPython, NEXO_HOME, migSchedule, LAUNCH_AGENTS, migOptionals);
        log("  All automated processes updated.");

        // Update version file
        fs.writeFileSync(versionFile, JSON.stringify({
          version: currentVersion,
          installed_at: installed.installed_at,
          updated_at: new Date().toISOString(),
          migrated_from: installedVersion,
        }, null, 2));
        syncRuntimePackageMetadata(path.join(__dirname, ".."), NEXO_HOME);

        // Save updated CLAUDE.md template as reference (don't overwrite user's)
        const templateSrc = path.join(__dirname, "..", "templates", "CLAUDE.md.template");
        if (fs.existsSync(templateSrc)) {
          const operatorName = installed.operator_name || "NEXO";
          let claudeMd = fs.readFileSync(templateSrc, "utf8")
            .replace(/\{\{NAME\}\}/g, operatorName)
            .replace(/\{\{NEXO_HOME\}\}/g, NEXO_HOME);
          fs.writeFileSync(path.join(NEXO_HOME, "CLAUDE.md.updated"), claudeMd);
          log(`  Updated CLAUDE.md template saved to ~/.nexo/CLAUDE.md.updated`);

          // Update CLAUDE.md version tracker (auto_update.py will handle section migration on next server start)
          const migClaudeMdVerMatch = claudeMd.match(/nexo-claude-md-version:\s*([\d.]+)/);
          if (migClaudeMdVerMatch) {
            const migDataDir = path.join(NEXO_HOME, "data");
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
        const migOperatorName = installed.operator_name || "NEXO";
        const migAliasName = migOperatorName.toLowerCase();
        if (migAliasName !== "nexo") {
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

        console.log("");
        log(`Migration complete: v${installedVersion} → v${currentVersion}`);
        log("Your data (memories, learnings, preferences) is untouched.");
        console.log("");
        rl.close();
        return;
      }

      // Same version — backfill crons/ if missing (for installs before crons was shipped)
      const cronsDest = path.join(NEXO_HOME, "crons");
      const cronsSrc = path.join(__dirname, "..", "src", "crons");
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

        const cronSyncPath = path.join(cronsSrc, "sync.py");
        const syncPython = findVenvPython(NEXO_HOME) || run("which python3") || "python3";
        if (fs.existsSync(cronSyncPath)) {
          const syncResult = spawnSync(syncPython, [cronSyncPath], {
            env: { ...process.env, NEXO_HOME, NEXO_CODE: path.join(__dirname, "..", "src") },
            stdio: "pipe",
            encoding: "utf8",
          });
          if (syncResult.status === 0) {
            log("Core crons reconciled with manifest.");
          } else {
            const syncErr = (syncResult.stderr || syncResult.stdout || "").trim();
            log(`Cron sync warning: ${syncErr || `exit ${syncResult.status}`}`);
          }
        }
      }

      // Same version — refresh packaged core skills/templates/runtime helpers too.
      const skillsCoreDest = path.join(NEXO_HOME, "skills-core");
      const skillsCoreSrc = path.join(__dirname, "..", "src", "skills");
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
        const srcFile = path.join(__dirname, "..", "src", fname);
        const destFile = path.join(NEXO_HOME, fname);
        if (fs.existsSync(srcFile)) {
          fs.copyFileSync(srcFile, destFile);
        }
      });
      syncRuntimePackageMetadata(path.join(__dirname, ".."), NEXO_HOME);

      const templatesSrc = path.join(__dirname, "..", "templates");
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

      logMacPermissionsNotice(NEXO_HOME, syncPython);

      log(`Already at v${currentVersion}. No migration needed.`);
      rl.close();
      return;
    } catch (e) {
      // Version file corrupt — proceed with fresh install
    }
  }

  // Find or install Python (platform-aware)
  let python = run("which python3");
  if (!python) {
    if (platform === "darwin") {
      // macOS: use Homebrew
      let hasBrew = run("which brew");
      if (!hasBrew) {
        log("Homebrew not found. Installing...");
        spawnSync("/bin/bash", ["-c", '$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)'], {
          stdio: "inherit",
        });
        hasBrew = run("which brew") || run("eval $(/opt/homebrew/bin/brew shellenv) && which brew");
      }
      if (hasBrew) {
        log("Python 3 not found. Installing via Homebrew...");
        spawnSync("brew", ["install", "python3"], { stdio: "inherit" });
        python = run("which python3");
      }
    } else if (platform === "linux") {
      // Linux: try apt or yum
      log("Python 3 not found. Attempting install...");
      if (run("which apt-get")) {
        spawnSync("sudo", ["apt-get", "install", "-y", "python3", "python3-pip", "python3-venv"], { stdio: "inherit" });
      } else if (run("which yum")) {
        spawnSync("sudo", ["yum", "install", "-y", "python3", "python3-pip"], { stdio: "inherit" });
      }
      python = run("which python3");
    }
    if (!python) {
      log("Python 3 not found and couldn't install automatically.");
      log(platform === "darwin" ? "Install it: brew install python3" : "Install it: sudo apt install python3");
      process.exit(1);
    }
  }
  const pyVersion = run(`${python} --version`);
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
      askAgentName: "  What should I call myself? (default: NEXO) > ",
      agentConfirm: (n) => `Got it. I'm ${n}.`,
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
      askAgentName: "  ¿Cómo quieres que me llame? (default: NEXO) > ",
      agentConfirm: (n) => `Perfecto, soy ${n}.`,
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
      askAgentName: "  Comment veux-tu m'appeler ? (défaut: NEXO) > ",
      agentConfirm: (n) => `C'est noté. Je suis ${n}.`,
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
      askAgentName: "  Wie soll ich heißen? (Standard: NEXO) > ",
      agentConfirm: (n) => `Alles klar. Ich bin ${n}.`,
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
      askAgentName: "  Come vuoi chiamarmi? (default: NEXO) > ",
      agentConfirm: (n) => `Perfetto, sono ${n}.`,
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
      askAgentName: "  Como queres que eu me chame? (padrão: NEXO) > ",
      agentConfirm: (n) => `Perfeito, sou ${n}.`,
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

  // Detect language from input or use default
  let lang = "en";
  let t = i18n.en;
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

  // Step 2: User's name (P2)
  let userName = "";
  if (!useDefaults) {
    const nameInput = await ask(t.askUserName);
    userName = nameInput.trim();
    if (userName) {
      log(t.userGreet(userName));
      console.log("");
    }
  }

  // Step 3: Agent name (P3)
  const name = useDefaults ? "" : await ask(t.askAgentName);
  const operatorName = name.trim() || "NEXO";
  log(t.agentConfirm(operatorName));
  console.log("");

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

  // Save calibration
  const calibration = {
    language: lang,
    user_name: userName,
    autonomy: autonomyLevel,
    communication: communicationStyle,
    honesty: honestyLevel,
    proactivity: proactivityLevel,
    error_handling: errorHandling,
    auto_install: "ask", // default, updated later if user answers P11
    calibrated_at: new Date().toISOString(),
  };
  // Ensure NEXO_HOME and brain dir exist before writing calibration
  fs.mkdirSync(NEXO_HOME, { recursive: true });
  fs.mkdirSync(path.join(NEXO_HOME, "brain"), { recursive: true });
  fs.writeFileSync(
    path.join(NEXO_HOME, "brain", "calibration.json"),
    JSON.stringify(calibration, null, 2)
  );

  // Step 5: Deep scan (P9)
  let doScan = false;
  let doCaffeinate = false;
  let doDashboard = false;
  let autoInstall = useDefaults ? "auto" : "ask";
  if (!useDefaults) {
    const scanAnswer = await ask(t.scanQ);
    doScan = scanAnswer.trim() === "1" || scanAnswer.trim().toLowerCase().startsWith("y") || scanAnswer.trim().toLowerCase().startsWith("s");
    console.log("");

    // Step 6: Caffeinate (P10) — macOS only
    if (platform === "darwin") {
      const caffeinateAnswer = await ask(t.caffeinateQ);
      doCaffeinate = caffeinateAnswer.trim() === "1" || caffeinateAnswer.trim().toLowerCase().startsWith("y") || caffeinateAnswer.trim().toLowerCase().startsWith("s");
      log(doCaffeinate ? `✓ ${t.caffYes}` : t.caffNo);
      console.log("");
    }

    // Step 6b: Dashboard — always-on web UI
    const dashAnswer = await ask(t.dashboardQ);
    doDashboard = dashAnswer.trim() === "1" || dashAnswer.trim().toLowerCase().startsWith("y") || dashAnswer.trim().toLowerCase().startsWith("s");
    log(doDashboard ? `✓ ${t.dashYes}` : t.dashNo);
    console.log("");

    // Step 7: Auto-install permission (P11)
    const autoInstallAnswer = await ask(t.autoInstallQ);
    autoInstall = (autoInstallAnswer.trim() === "1" || autoInstallAnswer.trim().toLowerCase().startsWith("y") || autoInstallAnswer.trim().toLowerCase().startsWith("s")) ? "auto" : "ask";
    calibration.auto_install = autoInstall;
    log(`✓ ${autoInstall === "auto" ? t.autoInstallYes : t.autoInstallNo}`);
    console.log("");
  } else {
    log("Skipping interactive setup (non-interactive mode).");
    console.log("");
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
  const venvPython = platform === "win32"
    ? path.join(venvPath, "Scripts", "python.exe")
    : path.join(venvPath, "bin", "python3");

  // Create venv if it doesn't exist
  if (!fs.existsSync(venvPython)) {
    log("  Creating Python virtual environment...");
    const venvResult = spawnSync(python, ["-m", "venv", venvPath], { stdio: "inherit" });
    if (venvResult.status !== 0) {
      log("Failed to create venv. Trying pip install directly...");
    }
  }

  // Use venv python if available, otherwise fall back to system python with --break-system-packages
  const pipPython = fs.existsSync(venvPython) ? venvPython : python;
  const requirementsFile = path.join(__dirname, "..", "src", "requirements.txt");
  const pipArgs = ["-m", "pip", "install", "--quiet", "-r", requirementsFile];
  if (!fs.existsSync(venvPython)) {
    pipArgs.push("--break-system-packages");  // Fallback for systems without venv
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

  // Step 4: Create ~/.nexo/
  log("Setting up NEXO home...");
  const dirs = [
    NEXO_HOME,
    path.join(NEXO_HOME, "bin"),
    path.join(NEXO_HOME, "plugins"),
    path.join(NEXO_HOME, "scripts"),
    path.join(NEXO_HOME, "skills"),
    path.join(NEXO_HOME, "skills-core"),
    path.join(NEXO_HOME, "skills-runtime"),
    path.join(NEXO_HOME, "logs"),
    path.join(NEXO_HOME, "backups"),
    path.join(NEXO_HOME, "coordination"),
    path.join(NEXO_HOME, "brain"),
    path.join(NEXO_HOME, "config"),
    path.join(NEXO_HOME, "operations"),
  ];
  dirs.forEach((d) => fs.mkdirSync(d, { recursive: true }));

  // Create default evolution-objective.json in brain/ if it doesn't exist
  const evoObjectivePath = path.join(NEXO_HOME, "brain", "evolution-objective.json");
  if (!fs.existsSync(evoObjectivePath)) {
    fs.writeFileSync(evoObjectivePath, JSON.stringify({
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
    }, null, 2));
    log("  Created default evolution-objective.json in brain/");
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
  syncRuntimePackageMetadata(path.join(__dirname, ".."), NEXO_HOME);

  // Copy source files
  log("Copying core runtime files...");
  const srcDir = path.join(__dirname, "..", "src");
  const pluginsSrcDir = path.join(srcDir, "plugins");
  const scriptsSrcDir = path.join(srcDir, "scripts");
  const skillsSrcDir = path.join(srcDir, "skills");
  const templateDir = path.join(__dirname, "..", "templates");

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
  const coreFiles = getCoreRuntimeFlatFiles();
  coreFiles.forEach((f) => {
    const src = path.join(srcDir, f);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(NEXO_HOME, f));
    }
  });

  // Runtime CLI wrapper lives in NEXO_HOME/bin so it survives npx installs.
  const runtimeCli = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    "",
    `RUNTIME_HOME="${NEXO_HOME}"`,
    'NEXO_HOME="$RUNTIME_HOME"',
    'export NEXO_HOME',
    'resolve_code_dir() {',
    '  if [ -n "${NEXO_CODE:-}" ] && [ -f "${NEXO_CODE%/}/cli.py" ]; then',
    '    printf \'%s\\n\' "${NEXO_CODE%/}"',
    '    return 0',
    '  fi',
    '  if [ -f "$NEXO_HOME/cli.py" ]; then',
    '    printf \'%s\\n\' "$NEXO_HOME"',
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
    'CLI_PY="$NEXO_CODE/cli.py"',
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
      copyDirRecursive(pkgSrc, path.join(NEXO_HOME, pkg));
    }
  });

  log("Copying plugins, scripts, and templates...");
  // Plugins (all .py files in plugins/)
  fs.mkdirSync(path.join(NEXO_HOME, "plugins"), { recursive: true });
  if (fs.existsSync(pluginsSrcDir)) {
    fs.readdirSync(pluginsSrcDir).filter(f => f.endsWith(".py") && !isDuplicateArtifactName(f, pluginsSrcDir)).forEach((f) => {
      fs.copyFileSync(path.join(pluginsSrcDir, f), path.join(NEXO_HOME, "plugins", f));
    });
  }

  // Scripts (all files + subdirectories like deep-sleep/)
  if (fs.existsSync(scriptsSrcDir)) {
    copyDirRecursive(scriptsSrcDir, path.join(NEXO_HOME, "scripts"));
    // Make .sh files executable
    const scriptsDest = path.join(NEXO_HOME, "scripts");
    fs.readdirSync(scriptsDest).filter(f => f.endsWith(".sh")).forEach(f => {
      fs.chmodSync(path.join(scriptsDest, f), "755");
    });
    syncWatchdogHashRegistry(NEXO_HOME);
  }

  // Core skills are shipped separately from personal skills.
  if (fs.existsSync(skillsSrcDir)) {
    copyDirRecursive(skillsSrcDir, path.join(NEXO_HOME, "skills-core"));
    log("  Core skills installed.");
  }

  // Dashboard (recursive — includes static/, templates/)
  const dashSrcDir = path.join(srcDir, "dashboard");
  if (fs.existsSync(dashSrcDir)) {
    copyDirRecursive(dashSrcDir, path.join(NEXO_HOME, "dashboard"));
    log("  Dashboard installed.");
  }

  // Rules directory
  const rulesSrcDir = path.join(srcDir, "rules");
  if (fs.existsSync(rulesSrcDir)) {
    copyDirRecursive(rulesSrcDir, path.join(NEXO_HOME, "rules"));
    log("  Rules installed.");
  }

  // Crons directory (manifest.json + sync.py — needed by catchup & watchdog)
  const cronsSrcDir = path.join(srcDir, "crons");
  if (fs.existsSync(cronsSrcDir)) {
    copyDirRecursive(cronsSrcDir, path.join(NEXO_HOME, "crons"));
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
    const hooksDest = path.join(NEXO_HOME, "hooks");
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
  fs.writeFileSync(path.join(NEXO_HOME, "brain", "personality.md"), personality);

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
      path.join(NEXO_HOME, "brain", "profile.json"),
      JSON.stringify(profileData, null, 2)
    );
    log(`Saved to ~/.nexo/brain/profile.json`);

  } else {
    // No scan — save minimal profile
    fs.writeFileSync(
      path.join(NEXO_HOME, "brain", "profile.json"),
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
  fs.writeFileSync(path.join(NEXO_HOME, "brain", "user-profile.md"), profileMd);

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
    args: [path.join(NEXO_HOME, "server.py")],
    env: {
      NEXO_HOME: NEXO_HOME,
      NEXO_NAME: operatorName,
    },
  };

  // Configure ALL 8 core hooks for session capture (Sensory Register)
  if (!settings.hooks) settings.hooks = {};

  // Hook scripts already copied above — just reference the dest dir
  const hooksDestDir = path.join(NEXO_HOME, "hooks");

  registerAllCoreHooks(settings, hooksDestDir, NEXO_HOME);

  const settingsDir = path.dirname(CLAUDE_SETTINGS);
  fs.mkdirSync(settingsDir, { recursive: true });
  fs.writeFileSync(CLAUDE_SETTINGS, JSON.stringify(settings, null, 2));
  log("MCP server + 8 core hooks configured in Claude Code settings.");

  const syncClientsScript = path.join(NEXO_HOME, "scripts", "nexo-sync-clients.py");
  if (fs.existsSync(syncClientsScript)) {
    const syncArgs = [
      syncClientsScript,
      "--nexo-home", NEXO_HOME,
      "--runtime-root", NEXO_HOME,
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

  const claudeCliPath = run("which claude") || "";
  if (claudeCliPath) {
    const cliPathFile = path.join(NEXO_HOME, "config", "claude-cli-path");
    fs.mkdirSync(path.dirname(cliPathFile), { recursive: true });
    fs.writeFileSync(cliPathFile, claudeCliPath.trim());
    log(`Claude CLI path saved: ${claudeCliPath.trim()}`);
  }

  // Step 7: Create schedule.json (only on fresh install) and install core processes
  log("Setting up automated processes...");
  let schedule = loadOrCreateSchedule(NEXO_HOME);
  schedule = applyClientSetupToSchedule(schedule, clientSetup);
  fs.writeFileSync(path.join(NEXO_HOME, "config", "schedule.json"), JSON.stringify(schedule, null, 2));
  schedule = await maybeConfigurePowerPolicy(schedule, useDefaults);
  schedule = await maybeConfigurePublicContribution(schedule, useDefaults);
  schedule = await maybeConfigureFullDiskAccess(schedule, useDefaults, python);
  const enabledOptionals = { dashboard: doDashboard, automation: schedule.automation_enabled !== false };
  if (isEphemeralInstall(NEXO_HOME)) {
    log("Ephemeral HOME/NEXO_HOME detected — skipping LaunchAgents installation.");
  } else {
    installAllProcesses(platform, python, NEXO_HOME, schedule, LAUNCH_AGENTS, enabledOptionals);
  }

  // Persist optional process preferences for auto-update
  try {
    const configDir = path.join(NEXO_HOME, "config");
    fs.mkdirSync(configDir, { recursive: true });
    const optFile = path.join(configDir, "optionals.json");
    fs.writeFileSync(optFile, JSON.stringify(enabledOptionals, null, 2));
  } catch {}

  // Note: prevent-sleep and tcc-approve are now part of ALL_PROCESSES
  // and installed by installAllProcesses() above. No separate caffeinate block needed.

  // Step 7b: macOS Keychain setup for headless automation
  await setupKeychainPassFile(NEXO_HOME);

  // Step 8: Create shell alias and add runtime CLI to PATH
  const aliasName = operatorName.toLowerCase();
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
    const dataDir = path.join(NEXO_HOME, "data");
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(path.join(dataDir, "claude_md_version.txt"), claudeMdVersionMatch[1]);
    log(`CLAUDE.md version tracker initialized: v${claudeMdVersionMatch[1]}`);
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

  rl.close();
}

main().catch((err) => {
  console.error("Setup failed:", err.message);
  process.exit(1);
});
