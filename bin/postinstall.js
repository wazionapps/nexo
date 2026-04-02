#!/usr/bin/env node
/**
 * postinstall — Runs automatically after npm install/update.
 *
 * - If NEXO is already installed (~/.nexo/version.json exists): auto-migrate
 * - If fresh install: print setup instructions only
 */

const fs = require("fs");
const path = require("path");

const NEXO_HOME = process.env.NEXO_HOME || path.join(require("os").homedir(), ".nexo");
const VERSION_FILE = path.join(NEXO_HOME, "version.json");

if (process.env.NEXO_SKIP_POSTINSTALL === "1") {
  // Called during rollback — skip migration to avoid loops
  process.exit(0);
}

if (fs.existsSync(VERSION_FILE)) {
  // Existing installation — run auto-migration silently
  const installed = JSON.parse(fs.readFileSync(VERSION_FILE, "utf8"));
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));

  if (installed.version === pkg.version) {
    // Same version, nothing to do
    process.exit(0);
  }

  console.log(`\n  NEXO Brain: upgrading v${installed.version} → v${pkg.version}...`);

  // Run the main installer in --yes mode (non-interactive)
  // It will detect the existing version and do migration only
  // Let errors propagate so npm reports the failure correctly
  const { execSync } = require("child_process");
  try {
    execSync(`node ${path.join(__dirname, "nexo-brain.js")} --yes`, {
      stdio: "inherit",
      env: { ...process.env, NEXO_POSTINSTALL: "1", NEXO_HOME: NEXO_HOME }
    });
  } catch (e) {
    console.error(`\n  NEXO Brain: migration FAILED — ${e.message}`);
    console.error("  Run 'nexo-brain' manually to complete setup.");
    process.exit(1);
  }
} else {
  // Fresh install — just show instructions
  console.log("\n  ╔════════════════════════════════════════════╗");
  console.log("  ║  NEXO Brain installed successfully!       ║");
  console.log("  ║                                           ║");
  console.log("  ║  Run 'nexo-brain' to complete setup.      ║");
  console.log("  ╚════════════════════════════════════════════╝\n");
}
