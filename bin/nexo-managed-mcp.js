#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

function nexoHome() {
  return process.env.NEXO_HOME || path.join(require("os").homedir(), ".nexo");
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return null;
  }
}

function findEntry(capabilityId) {
  const state = readJson(path.join(nexoHome(), "runtime", "managed-mcp", "installed-state.json"));
  const desired = state && state.desired && typeof state.desired === "object" ? state.desired : {};
  for (const clientEntries of Object.values(desired)) {
    if (!clientEntries || typeof clientEntries !== "object") continue;
    for (const entry of Object.values(clientEntries)) {
      const meta = entry && entry.nexo;
      if (meta && meta.capability_id === capabilityId) return entry;
    }
  }
  return null;
}

function providerEnv() {
  return {
    PATH: process.env.PATH || "",
    HOME: process.env.HOME || "",
    NEXO_HOME: nexoHome(),
  };
}

function stagedProviderCommand(providerId) {
  return path.join(nexoHome(), "runtime", "managed-mcp", "artifacts", providerId, "bin", providerId);
}

function spawnProvider(command, args, env) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env,
  });
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(typeof code === "number" ? code : 1);
  });
  child.on("error", (error) => {
    console.error(`NEXO managed MCP failed to start: ${error && error.message ? error.message : String(error)}`);
    process.exit(69);
  });
}

function main() {
  const [command, capabilityId] = process.argv.slice(2);
  if (command !== "run" || !capabilityId) {
    console.error("usage: nexo-managed-mcp run <capability_id>");
    process.exit(64);
  }
  const entry = findEntry(capabilityId);
  const meta = entry && entry.nexo ? entry.nexo : {};
  const providerId = meta.provider_id || "";
  if (!providerId) {
    console.error(`NEXO managed MCP capability '${capabilityId}' is not installed yet.`);
    process.exit(69);
  }
  const providerCommand = stagedProviderCommand(providerId);
  const env = providerEnv();
  if (!fs.existsSync(providerCommand)) {
    const providerPackage = String(meta.provider_package || "");
    const providerVersion = String(meta.provider_version || "");
    const providerBin = String(meta.provider_bin || providerId);
    if (!providerPackage || !providerVersion || providerVersion === "0.0.0-managed") {
      console.error(`NEXO managed MCP provider '${providerId}' is not staged and has no exact locked npm package.`);
      process.exit(69);
    }
    const npxBin = process.platform === "win32" ? "npx.cmd" : "npx";
    spawnProvider(npxBin, ["--yes", "--package", `${providerPackage}@${providerVersion}`, providerBin], env);
    return;
  }
  spawnProvider(providerCommand, [], env);
}

main();
