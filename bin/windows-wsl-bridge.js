const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const DEFAULT_LINUX_PATH_ENTRIES = [
  "/usr/local/sbin",
  "/usr/local/bin",
  "/usr/sbin",
  "/usr/bin",
  "/sbin",
  "/bin",
];

const PRESERVED_FLAG_ENV_KEYS = [
  "NEXO_DESKTOP_MANAGED",
  "NEXO_SKIP_SHELL_PROFILE",
  "NEXO_SKIP_MODEL_WARMUP",
  "NEXO_NO_LAUNCHD",
  "NEXO_INSTALL_NO_LAUNCHD",
];

function isWindowsHost(platform = process.platform) {
  return platform === "win32";
}

function isWindowsStylePath(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  return /^[A-Za-z]:[\\/]/.test(text) || text.startsWith("\\\\");
}

function parseWslUncPath(rawValue) {
  const value = String(rawValue || "").trim();
  const lowered = value.toLowerCase();
  if (!lowered.startsWith("\\\\wsl$\\") && !lowered.startsWith("\\\\wsl.localhost\\")) {
    return null;
  }
  const parts = value.split("\\").filter(Boolean);
  if (parts.length < 3) return null;
  return {
    distro: parts[1],
    linuxPath: `/${parts.slice(2).join("/")}`,
  };
}

function toWslPath(rawValue) {
  const value = String(rawValue || "").trim();
  if (!value) return "";
  if (value.startsWith("/")) return value;
  const unc = parseWslUncPath(value);
  if (unc) return unc.linuxPath;
  const normalized = value.replace(/\\/g, "/");
  const driveMatch = normalized.match(/^([A-Za-z]):(\/.*)?$/);
  if (driveMatch) {
    return `/mnt/${driveMatch[1].toLowerCase()}${driveMatch[2] || ""}`;
  }
  if (normalized.startsWith("//")) {
    return "";
  }
  return normalized;
}

function resolveExplicitLinuxPath(rawValue) {
  const value = String(rawValue || "").trim();
  if (!value) return "";
  if (value.startsWith("/")) return value;
  if (isWindowsStylePath(value)) return toWslPath(value);
  return value;
}

function resolveLinuxEnv(env = process.env) {
  const linuxEnv = {
    NEXO_WINDOWS_BRIDGE: "1",
    NEXO_WINDOWS_HOST: "1",
  };
  const explicitHome = resolveExplicitLinuxPath(env.NEXO_WSL_HOME);
  if (explicitHome) {
    linuxEnv.NEXO_HOME = explicitHome;
  } else if (env.NEXO_HOME && !isWindowsStylePath(env.NEXO_HOME)) {
    linuxEnv.NEXO_HOME = String(env.NEXO_HOME).trim();
  }
  const explicitCode = resolveExplicitLinuxPath(env.NEXO_WSL_CODE);
  if (explicitCode) {
    linuxEnv.NEXO_CODE = explicitCode;
  } else if (env.NEXO_CODE && !isWindowsStylePath(env.NEXO_CODE)) {
    linuxEnv.NEXO_CODE = String(env.NEXO_CODE).trim();
  }
  for (const key of PRESERVED_FLAG_ENV_KEYS) {
    const value = String(env[key] || "").trim();
    if (value) linuxEnv[key] = value;
  }
  return linuxEnv;
}

function resolveWindowsHostPathEnv(env = process.env) {
  const result = {};
  for (const key of ["LOCALAPPDATA", "APPDATA"]) {
    const value = String(env[key] || "").trim();
    if (!value) continue;
    if (isWindowsStylePath(value)) {
      result[key] = toWslPath(value);
    } else if (value.startsWith("/")) {
      result[key] = value;
    }
  }
  return result;
}

function uniqueValues(values = []) {
  const seen = new Set();
  return values.filter((value) => {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) return false;
    seen.add(text);
    return true;
  });
}

function buildManagedLinuxPath({ runtimeHome = "", linuxHome = "" } = {}) {
  return uniqueValues([
    runtimeHome ? `${runtimeHome}/bin` : "",
    runtimeHome ? `${runtimeHome}/runtime/bootstrap/npm-global/bin` : "",
    linuxHome ? path.posix.join(linuxHome, ".local", "bin") : "",
    ...DEFAULT_LINUX_PATH_ENTRIES,
  ]).join(":");
}

function inferLinuxUserHomeFromRuntimeHome(runtimeHome = "") {
  const value = String(runtimeHome || "").trim();
  if (!value.startsWith("/")) return "";
  if (path.posix.basename(value) === ".nexo") {
    return path.posix.dirname(value) || "";
  }
  return "";
}

function resolveLinuxUserHome({ env = process.env, linuxEnv = {}, defaultValue = "" } = {}) {
  const explicitHome = resolveExplicitLinuxPath(env.NEXO_WSL_USER_HOME);
  if (explicitHome) return explicitHome;
  const runtimeHome = inferLinuxUserHomeFromRuntimeHome(linuxEnv.NEXO_HOME || "");
  if (runtimeHome) return runtimeHome;
  const defaultHome = resolveExplicitLinuxPath(defaultValue);
  return defaultHome || "";
}

function shellSingleQuote(value) {
  return `'${String(value || "").replace(/'/g, `'\"'\"'`)}'`;
}

function resolveWslNodeBinary(env = process.env) {
  const explicitNode = resolveExplicitLinuxPath(env.NEXO_WSL_NODE);
  return explicitNode || "node";
}

function listRegisteredWslDistros(env = process.env) {
  // wsl --list --quiet emits UTF-16LE on Windows. Decode and clean.
  const result = spawnSync("wsl.exe", ["--list", "--quiet"], {
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error || result.status !== 0) return [];
  const buf = result.stdout || Buffer.alloc(0);
  const text = Buffer.isBuffer(buf) ? buf.toString("utf16le") : String(buf);
  return text
    .split(/\r?\n/)
    .map((line) => line.replace(/[\u0000\uFEFF ]/g, "").trim())
    .filter(Boolean);
}

function distroResponds(name, env = process.env) {
  if (!name) return false;
  const result = spawnSync("wsl.exe", ["-d", name, "--", "true"], {
    env,
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 30000,
  });
  return !result.error && result.status === 0;
}

function resolveWslDistro(scriptPath, env = process.env) {
  // WSL distros are per-Windows-user. The explicit name from env may not exist
  // for the current user; fall back to the first registered Ubuntu-* distro
  // that responds to a trivial command.
  const explicitDistro = String(env.NEXO_WSL_DISTRO || "").trim();
  const uncDistro = (() => {
    const unc = parseWslUncPath(scriptPath);
    return unc ? unc.distro : "";
  })();
  if (explicitDistro && distroResponds(explicitDistro, env)) return explicitDistro;
  if (uncDistro && distroResponds(uncDistro, env)) return uncDistro;
  const registered = listRegisteredWslDistros(env);
  for (const name of registered) {
    if (/^ubuntu(?:[-.\w]*)$/i.test(name) && distroResponds(name, env)) return name;
  }
  for (const name of registered) {
    if (distroResponds(name, env)) return name;
  }
  return explicitDistro || uncDistro || "";
}

function probeWslUserHome({ distro = "", env = process.env } = {}) {
  const probeArgs = [];
  if (distro) probeArgs.push("-d", distro);
  probeArgs.push("sh", "-lc", 'printf %s "$HOME"');
  const result = spawnSync("wsl.exe", probeArgs, {
    env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error || result.status !== 0) return "";
  const lines = String(result.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const candidate = lines.length ? lines[lines.length - 1] : "";
  return candidate.startsWith("/") ? candidate : "";
}

function buildWslExecSpec({
  scriptPath,
  args = [],
  env = process.env,
  platform = process.platform,
  linuxHome = "",
}) {
  if (!isWindowsHost(platform)) return null;
  const translatedScriptPath = toWslPath(scriptPath);
  if (!translatedScriptPath) {
    return { error: `Unable to translate Windows path to WSL path: ${scriptPath}` };
  }

  const linuxEnv = resolveLinuxEnv(env);
  const wslArgs = [];
  const distro = resolveWslDistro(scriptPath, env);
  if (distro) wslArgs.push("-d", distro);

  // Run as root: the freshly-created `nexo` user in Ubuntu-24.04 distros
  // has profile state that silently breaks variable assignments inside
  // sh -c scripts (verified empirically). Root is unaffected. Brain's own
  // installer writes its outputs as root then chowns them where needed.
  wslArgs.push("-u", "root");
  wslArgs.push("--");

  const resolvedLinuxHome = resolveLinuxUserHome({ env, linuxEnv, defaultValue: linuxHome });
  const managedLinuxPath = buildManagedLinuxPath({
    runtimeHome: linuxEnv.NEXO_HOME || "",
    linuxHome: resolvedLinuxHome,
  });

  // env -i: start with empty environment so distro/profile state can't bleed
  // into the staged script. Set only what we need explicitly.
  wslArgs.push("env", "-i");
  wslArgs.push("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin");
  wslArgs.push(`NEXO_MANAGED_PATH=${managedLinuxPath}`);
  for (const [key, value] of Object.entries(linuxEnv)) {
    wslArgs.push(`${key}=${value}`);
  }
  const windowsHostPathEnv = resolveWindowsHostPathEnv(env);
  for (const [key, value] of Object.entries(windowsHostPathEnv)) {
    wslArgs.push(`${key}=${value}`);
  }

  // Build the staging shell script. Stages the bundle from /mnt/c (DrvFs/9P)
  // to /tmp (native ext4) BEFORE invoking node. Without staging, node hangs
  // reading the module recursively over 9P.
  const linuxBundleRoot = toWslPath(path.dirname(path.dirname(scriptPath)));
  const scriptRelPath = path.posix.join("bin", path.basename(scriptPath));
  const argsLiteral = args.map((value) => shellSingleQuote(String(value))).join(" ");
  const nodeBinary = shellSingleQuote(resolveWslNodeBinary(env));

  const shellScript = [];
  if (resolvedLinuxHome) {
    shellScript.push(`export HOME=${shellSingleQuote(resolvedLinuxHome)}`);
  }
  shellScript.push('export PATH="${NEXO_MANAGED_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"');
  shellScript.push("unset USERPROFILE HOMEDRIVE HOMEPATH");
  if (resolvedLinuxHome) {
    shellScript.push('mkdir -p "$HOME" >/dev/null 2>&1 || true');
    shellScript.push('cd "$HOME" >/dev/null 2>&1 || true');
  }
  shellScript.push("STAGE_ROOT=/tmp/nexo-brain-stage");
  shellScript.push('rm -rf "$STAGE_ROOT" 2>/dev/null');
  shellScript.push('mkdir -p "$STAGE_ROOT" || exit 11');
  shellScript.push(
    `tar --exclude=__pycache__ --exclude='*.pyc' --exclude='*.db' --exclude=.git ` +
    `-C ${shellSingleQuote(linuxBundleRoot)} -cf - . | tar -C "$STAGE_ROOT" -xf - || exit 12`
  );
  shellScript.push(`exec ${nodeBinary} "$STAGE_ROOT/${scriptRelPath}" ${argsLiteral}`);

  // Persist the script to a Windows-side path and invoke `dash <path>` (NOT
  // -c). Passing a multi-KB script via -c got mangled in some Ubuntu-24.04
  // distros — file-based invocation bypasses any cmdline escape pathway.
  const winScriptPath = path.join(
    os.homedir(),
    ".nexo",
    "runtime",
    "bootstrap",
    "logs",
    "bootstrap-script.sh"
  );
  try {
    fs.mkdirSync(path.dirname(winScriptPath), { recursive: true });
    fs.writeFileSync(
      winScriptPath,
      "#!/bin/dash\n" + shellScript.join("\n") + "\n",
      { encoding: "utf8" }
    );
  } catch (_) {}
  const linuxScriptPath = toWslPath(winScriptPath);
  wslArgs.push("/bin/dash", linuxScriptPath);

  return {
    command: "wsl.exe",
    args: wslArgs,
    linuxEnv,
    windowsHostPathEnv,
    managedLinuxPath,
    translatedScriptPath,
  };
}

function logWslBridgeFailure(label, message) {
  console.error(`${label} could not start through WSL.`);
  console.error(message);
  console.error("Install WSL: https://learn.microsoft.com/en-us/windows/wsl/install");
  console.error("Then run the same command again from PowerShell/CMD or inside your Ubuntu WSL shell.");
}

function runViaWsl({ scriptPath, args = [], env = process.env, platform = process.platform, stdio = "inherit", label = "NEXO" }) {
  const distro = resolveWslDistro(scriptPath, env);
  const linuxHome = probeWslUserHome({ distro, env });
  const spec = buildWslExecSpec({ scriptPath, args, env, platform, linuxHome });
  if (!spec) return null;
  if (spec.error) {
    logWslBridgeFailure(label, spec.error);
    return { status: 1 };
  }
  const result = spawnSync(spec.command, spec.args, {
    stdio,
    env,
    windowsHide: platform === "win32",
  });
  if (result.error && result.error.code === "ENOENT") {
    logWslBridgeFailure(label, "The Windows host does not have `wsl.exe` available.");
    return { status: 1 };
  }
  return result;
}

module.exports = {
  buildWslExecSpec,
  inferLinuxUserHomeFromRuntimeHome,
  isWindowsHost,
  isWindowsStylePath,
  parseWslUncPath,
  probeWslUserHome,
  resolveLinuxEnv,
  resolveLinuxUserHome,
  resolveWindowsHostPathEnv,
  runViaWsl,
  toWslPath,
};
