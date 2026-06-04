#!/usr/bin/env node
/**
 * postinstall — Runs automatically after npm install/update.
 *
 * - If NEXO is already installed (~/.nexo/version.json exists): auto-migrate
 * - If fresh install: print setup instructions only
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const NEXO_HOME = process.env.NEXO_HOME || path.join(require("os").homedir(), ".nexo");
const VERSION_FILE = path.join(NEXO_HOME, "version.json");
const INSTALLER = path.join(__dirname, "nexo-brain.js");
const REPAIR_BASELINE_FILE = "last-repair-baseline.json";

if (process.env.NEXO_SKIP_POSTINSTALL === "1") {
  // Called during rollback — skip migration to avoid loops
  process.exit(0);
}

function runModelWarmup(reason) {
  if (process.env.NEXO_SKIP_MODEL_WARMUP === "1") {
    console.log(`\n  NEXO Brain: model warmup skipped by NEXO_SKIP_MODEL_WARMUP (${reason}).`);
    return;
  }
  console.log(`\n  NEXO Brain: warming up local models (${reason})...`);
  execFileSync(process.execPath, [INSTALLER, "warmup-models", "--postinstall"], {
    stdio: "inherit",
    env: { ...process.env, NEXO_POSTINSTALL: "1", NEXO_HOME: NEXO_HOME }
  });
}

function runtimeRepairBaselineDir(nexoHome = NEXO_HOME) {
  const canonical = path.join(nexoHome, "runtime", "operations");
  const legacy = path.join(nexoHome, "operations");
  if (!fs.existsSync(path.join(nexoHome, "runtime")) && fs.existsSync(legacy)) {
    return legacy;
  }
  return canonical;
}

function stampRuntimeRepairBaseline(source = "bin.postinstall") {
  const operationsDir = runtimeRepairBaselineDir(NEXO_HOME);
  fs.mkdirSync(operationsDir, { recursive: true });
  const now = new Date();
  const body = JSON.stringify({
    last_repair_epoch: now.getTime() / 1000,
    last_repair_at: now.toISOString().replace(/\.\d{3}Z$/, "Z"),
    source,
    reason: "verified runtime repair baseline after installer/postinstall repair",
  }, null, 2) + "\n";
  const baselinePath = path.join(operationsDir, REPAIR_BASELINE_FILE);
  fs.writeFileSync(baselinePath, body);

  const legacyDir = path.join(NEXO_HOME, "operations");
  const legacyPath = path.join(legacyDir, REPAIR_BASELINE_FILE);
  if (legacyPath !== baselinePath && fs.existsSync(legacyDir)) {
    try {
      fs.writeFileSync(legacyPath, body);
    } catch (_) {}
  }
}

if (fs.existsSync(VERSION_FILE)) {
  // Existing installation — run auto-migration silently
  const installed = JSON.parse(fs.readFileSync(VERSION_FILE, "utf8"));
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));

  if (installed.version === pkg.version) {
    stampRuntimeRepairBaseline("bin.postinstall.same-version");
    process.exit(0);
  }

  console.log(`\n  NEXO Brain: upgrading v${installed.version} → v${pkg.version}...`);

  // Run the main installer in --yes mode (non-interactive)
  // It will detect the existing version and do migration only
  // Let errors propagate so npm reports the failure correctly
  try {
    execFileSync(process.execPath, [INSTALLER, "--yes"], {
      stdio: "inherit",
      env: { ...process.env, NEXO_POSTINSTALL: "1", NEXO_HOME: NEXO_HOME }
    });
  } catch (e) {
    console.error(`\n  NEXO Brain: migration FAILED — ${e.message}`);
    console.error("  Run 'nexo-brain' manually to complete setup.");
    process.exit(1);
  }
} else {
  try {
    runModelWarmup("fresh install");
  } catch (e) {
    console.error(`\n  NEXO Brain: model warmup FAILED — ${e.message}`);
    console.error("  Set NEXO_SKIP_MODEL_WARMUP=1 only for CI/offline testing, then run 'nexo-brain warmup-models'.");
    process.exit(1);
  }

  // Fresh install — just show instructions
  console.log("\n  ╔════════════════════════════════════════════╗");
  console.log("  ║  NEXO Brain installed successfully!       ║");
  console.log("  ║                                           ║");
  console.log("  ║  Run 'nexo-brain' to complete setup.      ║");
  console.log("  ╚════════════════════════════════════════════╝\n");
}
