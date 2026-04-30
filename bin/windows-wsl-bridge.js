const { spawnSync } = require("child_process");
const path = require("path");

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

  return linuxEnv;
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

function resolveWslNodeBinary(env = process.env) {
  const explicitNode = resolveExplicitLinuxPath(env.NEXO_WSL_NODE);
  return explicitNode || "node";
}

function resolveWslDistro(scriptPath, env = process.env) {
  const explicitDistro = String(env.NEXO_WSL_DISTRO || "").trim();
  if (explicitDistro) return explicitDistro;
  const unc = parseWslUncPath(scriptPath);
  return unc ? unc.distro : "";
}

function probeWslUserHome({ distro = "", env = process.env } = {}) {
  const probeArgs = [];
  if (distro) {
    probeArgs.push("-d", distro);
  }
  probeArgs.push("--cd", "~", "--exec", "pwd");

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
    return {
      error: `Unable to translate Windows path to WSL path: ${scriptPath}`,
    };
  }

  const linuxEnv = resolveLinuxEnv(env);
  const wslArgs = [];
  const distro = resolveWslDistro(scriptPath, env);
  if (distro) {
    wslArgs.push("-d", distro);
  }
  const resolvedLinuxHome = resolveLinuxUserHome({ env, linuxEnv, defaultValue: linuxHome });

  wslArgs.push("--cd", resolvedLinuxHome || "~");

  wslArgs.push(
    "--exec",
    "env",
    "-u",
    "HOME",
    "-u",
    "NEXO_HOME",
    "-u",
    "NEXO_CODE",
    "-u",
    "NEXO_WSL_HOME",
    "-u",
    "NEXO_WSL_CODE",
    "-u",
    "USERPROFILE",
    "-u",
    "HOMEDRIVE",
    "-u",
    "HOMEPATH"
  );

  if (resolvedLinuxHome) {
    wslArgs.push(`HOME=${resolvedLinuxHome}`);
  }
  for (const [key, value] of Object.entries(linuxEnv)) {
    wslArgs.push(`${key}=${value}`);
  }

  wslArgs.push(resolveWslNodeBinary(env), translatedScriptPath, ...args);

  return {
    command: "wsl.exe",
    args: wslArgs,
    linuxEnv,
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

  const result = spawnSync(spec.command, spec.args, { stdio, env });
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
  runViaWsl,
  toWslPath,
};
