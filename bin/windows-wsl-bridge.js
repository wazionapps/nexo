const { spawnSync } = require("child_process");

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

function buildWslExecSpec({ scriptPath, args = [], env = process.env, platform = process.platform }) {
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

  wslArgs.push(
    "--exec",
    "env",
    "-u",
    "NEXO_HOME",
    "-u",
    "NEXO_CODE",
    "-u",
    "NEXO_WSL_HOME",
    "-u",
    "NEXO_WSL_CODE"
  );

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
  const spec = buildWslExecSpec({ scriptPath, args, env, platform });
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
  isWindowsHost,
  isWindowsStylePath,
  parseWslUncPath,
  resolveLinuxEnv,
  runViaWsl,
  toWslPath,
};
