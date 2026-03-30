#!/usr/bin/env node
/**
 * postinstall — Runs automatically after npm install/update.
 *
 * - If NEXO is already installed (~/.nexo/version.json exists): auto-migrate
 * - If fresh install: print setup instructions only
 */

const fs = require("fs");
const path = require("path");

const NEXO_HOME = path.join(require("os").homedir(), ".nexo");
const VERSION_FILE = path.join(NEXO_HOME, "version.json");

if (fs.existsSync(VERSION_FILE)) {
  // Existing installation — run auto-migration silently
  try {
    const installed = JSON.parse(fs.readFileSync(VERSION_FILE, "utf8"));
    const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));

    if (installed.version === pkg.version) {
      // Same version, nothing to do
      process.exit(0);
    }

    console.log(`\n  NEXO Brain: upgrading v${installed.version} → v${pkg.version}...`);

    // Run the main installer in --yes mode (non-interactive)
    // It will detect the existing version and do migration only
    const { execSync } = require("child_process");
    execSync(`node ${path.join(__dirname, "nexo-brain.js")} --yes`, {
      stdio: "inherit",
      env: { ...process.env, NEXO_POSTINSTALL: "1" }
    });
  } catch (e) {
    console.error(`  NEXO Brain: migration warning — ${e.message}`);
    console.log("  Run 'nexo-brain' manually to complete setup.");
  }
} else {
  // Fresh install — just show instructions
  console.log("\n  ╔════════════════════════════════════════════╗");
  console.log("  ║  NEXO Brain installed successfully!       ║");
  console.log("  ║                                           ║");
  console.log("  ║  Run 'nexo-brain' to complete setup.      ║");
  console.log("  ╚════════════════════════════════════════════╝\n");
}
