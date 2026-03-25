#!/usr/bin/env node
/**
 * create-nexo — Interactive installer for NEXO cognitive co-operator.
 *
 * Usage: npx create-nexo
 *
 * What it does:
 * 1. Asks for the co-operator's name
 * 2. Asks permission to scan the workspace
 * 3. Installs Python dependencies (fastembed, numpy, mcp)
 * 4. Creates ~/.nexo/ with DB, personality, and config
 * 5. Configures Claude Code MCP settings
 * 6. Creates LaunchAgents for macOS automated processes
 * 7. Generates CLAUDE.md with the operator's instructions
 */

const { execSync, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const NEXO_HOME = path.join(require("os").homedir(), ".nexo");
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

async function main() {
  console.log("");
  console.log(
    "  ╔══════════════════════════════════════════════════════════╗"
  );
  console.log(
    "  ║  NEXO — Cognitive Co-Operator for Claude Code          ║"
  );
  console.log(
    "  ║  Atkinson-Shiffrin Memory | RAG | Trust Score           ║"
  );
  console.log(
    "  ╚══════════════════════════════════════════════════════════╝"
  );
  console.log("");

  // Check prerequisites
  const platform = process.platform;
  if (platform !== "darwin" && platform !== "linux" && platform !== "win32") {
    log(`Unsupported platform: ${platform}. NEXO supports macOS, Linux, and Windows.`);
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

        // Update hooks
        const hooksSrc = path.join(__dirname, "..", "src", "hooks");
        const hooksDest = path.join(NEXO_HOME, "hooks");
        fs.mkdirSync(hooksDest, { recursive: true });
        ["session-start.sh", "capture-session.sh", "session-stop.sh", "pre-compact.sh", "caffeinate-guard.sh"].forEach((h) => {
          const src = path.join(hooksSrc, h);
          const dest = path.join(hooksDest, h);
          if (fs.existsSync(src)) {
            fs.copyFileSync(src, dest);
            fs.chmodSync(dest, "755");
          }
        });
        log("  Hooks updated.");

        // Update core Python files
        const srcDir = path.join(__dirname, "..", "src");
        ["server.py", "db.py", "plugin_loader.py", "cognitive.py",
         "knowledge_graph.py", "kg_populate.py", "maintenance.py", "storage_router.py",
         "tools_sessions.py", "tools_coordination.py", "tools_reminders.py",
         "tools_reminders_crud.py", "tools_learnings.py", "tools_credentials.py",
         "tools_task_history.py", "tools_menu.py"].forEach((f) => {
          const src = path.join(srcDir, f);
          if (fs.existsSync(src)) {
            fs.copyFileSync(src, path.join(NEXO_HOME, f));
          }
        });
        log("  Core files updated.");

        // Update plugins
        const pluginsSrc = path.join(srcDir, "plugins");
        const pluginsDest = path.join(NEXO_HOME, "plugins");
        fs.mkdirSync(pluginsDest, { recursive: true });
        if (fs.existsSync(pluginsSrc)) {
          fs.readdirSync(pluginsSrc).filter(f => f.endsWith(".py")).forEach((f) => {
            fs.copyFileSync(path.join(pluginsSrc, f), path.join(pluginsDest, f));
          });
        }
        log("  Plugins updated.");

        // Update dashboard
        const dashSrc = path.join(srcDir, "dashboard");
        const dashDest = path.join(NEXO_HOME, "dashboard");
        if (fs.existsSync(dashSrc)) {
          fs.mkdirSync(dashDest, { recursive: true });
          const copyDir = (src, dest) => {
            fs.readdirSync(src).forEach(item => {
              const srcPath = path.join(src, item);
              const destPath = path.join(dest, item);
              if (fs.statSync(srcPath).isDirectory()) {
                fs.mkdirSync(destPath, { recursive: true });
                copyDir(srcPath, destPath);
              } else {
                fs.copyFileSync(srcPath, destPath);
              }
            });
          };
          copyDir(dashSrc, dashDest);
          log("  Dashboard updated.");
        }

        // Update scripts
        const scriptsSrc = path.join(srcDir, "scripts");
        const scriptsDest = path.join(NEXO_HOME, "scripts");
        fs.mkdirSync(scriptsDest, { recursive: true });
        if (fs.existsSync(scriptsSrc)) {
          fs.readdirSync(scriptsSrc).filter(f => f.endsWith(".py") || f.endsWith(".sh")).forEach((f) => {
            fs.copyFileSync(path.join(scriptsSrc, f), path.join(scriptsDest, f));
          });
        }
        log("  Scripts updated.");

        // Add PreCompact hook to settings.json if missing
        let settings = {};
        if (fs.existsSync(CLAUDE_SETTINGS)) {
          try { settings = JSON.parse(fs.readFileSync(CLAUDE_SETTINGS, "utf8")); } catch {}
        }
        if (settings.hooks && !settings.hooks.PreCompact) {
          settings.hooks.PreCompact = [];
        }
        if (settings.hooks && settings.hooks.PreCompact) {
          const hookPath = path.join(hooksDest, "pre-compact.sh");
          if (!settings.hooks.PreCompact.some((h) => h.command && h.command.includes("pre-compact.sh"))) {
            settings.hooks.PreCompact.push({
              type: "command",
              command: `bash ${hookPath}`,
            });
            fs.writeFileSync(CLAUDE_SETTINGS, JSON.stringify(settings, null, 2));
            log("  PreCompact hook added to Claude Code settings.");
          }
        }

        // Update version file
        fs.writeFileSync(versionFile, JSON.stringify({
          version: currentVersion,
          installed_at: installed.installed_at,
          updated_at: new Date().toISOString(),
          migrated_from: installedVersion,
        }, null, 2));

        // Save updated CLAUDE.md template as reference (don't overwrite user's)
        const templateSrc = path.join(__dirname, "..", "templates", "CLAUDE.md.template");
        if (fs.existsSync(templateSrc)) {
          const operatorName = installed.operator_name || "NEXO";
          let claudeMd = fs.readFileSync(templateSrc, "utf8")
            .replace(/\{\{NAME\}\}/g, operatorName)
            .replace(/\{\{NEXO_HOME\}\}/g, NEXO_HOME);
          fs.writeFileSync(path.join(NEXO_HOME, "CLAUDE.md.updated"), claudeMd);
          log(`  Updated CLAUDE.md template saved to ~/.nexo/CLAUDE.md.updated`);
          log(`  Review and merge changes into your ~/.claude/CLAUDE.md if desired.`);
        }

        console.log("");
        log(`Migration complete: v${installedVersion} → v${currentVersion}`);
        log("Your data (memories, learnings, preferences) is untouched.");
        console.log("");
        rl.close();
        return;
      }
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

  // Find or install Claude Code
  let claudeInstalled = run("which claude");
  if (!claudeInstalled) {
    log("Claude Code not found. Installing...");
    // Try npx first (no sudo needed), then npm -g as fallback
    spawnSync("npx", ["-y", "@anthropic-ai/claude-code", "--version"], { stdio: "pipe", timeout: 60000 });
    claudeInstalled = run("which claude") || run("npx -y @anthropic-ai/claude-code --version");
    if (!claudeInstalled) {
      // Fallback: npm -g (may need sudo on Linux)
      const npmCmd = platform === "linux" ? "sudo" : "npm";
      const npmArgs = platform === "linux" ? ["npm", "install", "-g", "@anthropic-ai/claude-code"] : ["install", "-g", "@anthropic-ai/claude-code"];
      spawnSync(npmCmd, npmArgs, { stdio: "inherit" });
      claudeInstalled = run("which claude");
    }
    if (!claudeInstalled) {
      log("Could not install Claude Code automatically.");
      log("Install it manually: npm install -g @anthropic-ai/claude-code");
      log("(On Linux you may need: sudo npm install -g @anthropic-ai/claude-code)");
      process.exit(1);
    }
    log("Claude Code installed successfully.");
  } else {
    log("Claude Code detected.");
  }
  console.log("");

  // Step 1: Name
  const name = await ask("  How should I call myself? (default: NEXO) > ");
  const operatorName = name.trim() || "NEXO";
  log(`Got it. I'm ${operatorName}.`);
  console.log("");

  // Step 2: Personality Calibration
  log("Let's calibrate my personality to work best with you.");
  log("(These can be changed anytime via nexo_preference_set)");
  console.log("");

  const autonomyAnswer = await ask(
    "  How autonomous should I be?\n" +
    "    1. Ask before most actions (conservative)\n" +
    "    2. Act on routine tasks, ask on important ones (balanced)\n" +
    "    3. Act first, inform after — only ask when truly uncertain (full autonomy)\n" +
    "  > "
  );
  const autonomyLevel = ["conservative", "balanced", "full"][parseInt(autonomyAnswer.trim()) - 1] || "balanced";

  const communicationAnswer = await ask(
    "\n  How should I communicate?\n" +
    "    1. Concise — just results, no filler (expert user)\n" +
    "    2. Balanced — brief explanations when useful\n" +
    "    3. Detailed — explain reasoning and trade-offs\n" +
    "  > "
  );
  const communicationStyle = ["concise", "balanced", "detailed"][parseInt(communicationAnswer.trim()) - 1] || "balanced";

  const honestyAnswer = await ask(
    "\n  When I disagree with your approach, should I:\n" +
    "    1. Push back firmly and explain why\n" +
    "    2. Mention it briefly but follow your lead\n" +
    "    3. Just do what you ask\n" +
    "  > "
  );
  const honestyLevel = ["firm-pushback", "mention-and-follow", "just-execute"][parseInt(honestyAnswer.trim()) - 1] || "firm-pushback";

  const proactivityAnswer = await ask(
    "\n  How proactive should I be?\n" +
    "    1. Only do what's asked\n" +
    "    2. Suggest improvements when I spot them\n" +
    "    3. Fix things I notice without asking, and propose optimizations\n" +
    "  > "
  );
  const proactivityLevel = ["reactive", "suggestive", "proactive"][parseInt(proactivityAnswer.trim()) - 1] || "proactive";

  const errorAnswer = await ask(
    "\n  When I make a mistake, how should I handle it?\n" +
    "    1. Brief acknowledgment, fix it, move on\n" +
    "    2. Explain what went wrong and what I learned\n" +
    "  > "
  );
  const errorHandling = ["brief-fix", "explain-and-learn"][parseInt(errorAnswer.trim()) - 1] || "brief-fix";

  console.log("");
  log(`Calibrated: autonomy=${autonomyLevel}, communication=${communicationStyle}, honesty=${honestyLevel}, proactivity=${proactivityLevel}, errors=${errorHandling}`);
  console.log("");

  // Save calibration
  const calibration = {
    autonomy: autonomyLevel,
    communication: communicationStyle,
    honesty: honestyLevel,
    proactivity: proactivityLevel,
    error_handling: errorHandling,
    calibrated_at: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(NEXO_HOME, "brain", "calibration.json"),
    JSON.stringify(calibration, null, 2)
  );

  // Step 3: Permission to scan
  const scanAnswer = await ask(
    "  Can I explore your workspace to learn about your projects? (y/n) > "
  );
  const doScan = scanAnswer.trim().toLowerCase().startsWith("y");
  console.log("");

  // Step 2b: Keep Mac awake for nocturnal processes?
  const caffeinateAnswer = await ask(
    "  Keep Mac awake so my cognitive processes run on schedule? (y/n) > "
  );
  const doCaffeinate = caffeinateAnswer.trim().toLowerCase().startsWith("y");
  console.log("");

  // Step 3: Install Python dependencies
  log("Installing cognitive engine dependencies...");
  const pipInstall = spawnSync(
    python,
    [
      "-m",
      "pip",
      "install",
      "--quiet",
      "fastembed",
      "numpy",
      "mcp[cli]",
    ],
    { stdio: "inherit" }
  );
  if (pipInstall.status !== 0) {
    log("Failed to install Python dependencies. Check pip.");
    process.exit(1);
  }
  log("Dependencies installed.");

  // Step 4: Create ~/.nexo/
  log("Setting up NEXO home...");
  const dirs = [
    NEXO_HOME,
    path.join(NEXO_HOME, "plugins"),
    path.join(NEXO_HOME, "scripts"),
    path.join(NEXO_HOME, "logs"),
    path.join(NEXO_HOME, "backups"),
    path.join(NEXO_HOME, "coordination"),
    path.join(NEXO_HOME, "brain"),
  ];
  dirs.forEach((d) => fs.mkdirSync(d, { recursive: true }));

  // Write version file for auto-update tracking
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
  fs.writeFileSync(
    path.join(NEXO_HOME, "version.json"),
    JSON.stringify({
      version: pkg.version,
      installed_at: new Date().toISOString(),
      operator_name: operatorName,
      files_updated: 0,
    }, null, 2)
  );

  // Copy source files
  const srcDir = path.join(__dirname, "..", "src");
  const scriptsSrcDir = path.join(__dirname, "..", "src", "scripts");
  const pluginsSrcDir = path.join(__dirname, "..", "src", "plugins");
  const templateDir = path.join(__dirname, "..", "templates");

  // Core files
  const coreFiles = [
    "server.py",
    "db.py",
    "plugin_loader.py",
    "cognitive.py",
    "tools_sessions.py",
    "tools_coordination.py",
    "tools_reminders.py",
    "tools_reminders_crud.py",
    "tools_learnings.py",
    "tools_credentials.py",
    "tools_task_history.py",
    "tools_menu.py",
  ];
  coreFiles.forEach((f) => {
    const src = path.join(srcDir, f);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(NEXO_HOME, f));
    }
  });

  // Plugins
  const pluginFiles = [
    "__init__.py",
    "guard.py",
    "episodic_memory.py",
    "cognitive_memory.py",
    "entities.py",
    "preferences.py",
    "agents.py",
    "backup.py",
    "evolution.py",
  ];
  pluginFiles.forEach((f) => {
    const src = path.join(pluginsSrcDir, f);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(NEXO_HOME, "plugins", f));
    }
  });

  // Scripts
  const scriptFiles = fs
    .readdirSync(scriptsSrcDir || ".")
    .filter((f) => f.endsWith(".py"));
  scriptFiles.forEach((f) => {
    const src = path.join(scriptsSrcDir, f);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(NEXO_HOME, "scripts", f));
    }
  });

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

  // Generate user profile
  const profile = `# User Profile

Created: ${new Date().toISOString().split("T")[0]}
Operator name: ${operatorName}

## Observed preferences
(${operatorName} will learn these over time)

## Work patterns
(${operatorName} will observe and record these)
`;
  fs.writeFileSync(path.join(NEXO_HOME, "brain", "user-profile.md"), profile);

  // Step 5: Scan workspace
  if (doScan) {
    log("Scanning workspace...");
    const cwd = process.cwd();
    const findings = [];

    // Git repos
    const gitDirs = run(
      `find "${cwd}" -maxdepth 3 -name ".git" -type d 2>/dev/null`
    );
    if (gitDirs) {
      const repos = gitDirs.split("\n").filter(Boolean);
      findings.push(`${repos.length} git repositories`);
    }

    // Package managers
    if (fs.existsSync(path.join(cwd, "package.json")))
      findings.push("Node.js project detected");
    if (fs.existsSync(path.join(cwd, "requirements.txt")))
      findings.push("Python project detected");
    if (fs.existsSync(path.join(cwd, "Cargo.toml")))
      findings.push("Rust project detected");
    if (fs.existsSync(path.join(cwd, "go.mod")))
      findings.push("Go project detected");

    // Config files
    if (fs.existsSync(path.join(cwd, ".env")))
      findings.push(".env file found (will NOT read contents)");

    if (findings.length > 0) {
      log("Found:");
      findings.forEach((f) => log(`  - ${f}`));
    } else {
      log("No projects detected in current directory.");
    }

    // Save scan results
    fs.writeFileSync(
      path.join(NEXO_HOME, "brain", "workspace-scan.json"),
      JSON.stringify(
        { scanned_at: new Date().toISOString(), cwd, findings },
        null,
        2
      )
    );
  }

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

  // Configure hooks for session capture (Sensory Register)
  if (!settings.hooks) settings.hooks = {};

  // Copy hook scripts to NEXO_HOME
  const hooksSrcDir = path.join(__dirname, "..", "src", "hooks");
  const hooksDestDir = path.join(NEXO_HOME, "hooks");
  fs.mkdirSync(hooksDestDir, { recursive: true });
  ["session-start.sh", "capture-session.sh", "session-stop.sh", "pre-compact.sh"].forEach((h) => {
    const src = path.join(hooksSrcDir, h);
    const dest = path.join(hooksDestDir, h);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, dest);
      fs.chmodSync(dest, "755");
    }
  });

  // SessionStart hook
  if (!settings.hooks.SessionStart) settings.hooks.SessionStart = [];
  const startHook = {
    type: "command",
    command: `bash ${path.join(hooksDestDir, "session-start.sh")}`,
  };
  if (!settings.hooks.SessionStart.some((h) => h.command && h.command.includes("session-start.sh"))) {
    settings.hooks.SessionStart.push(startHook);
  }

  // PostToolUse hook (captures tool usage to session_buffer)
  if (!settings.hooks.PostToolUse) settings.hooks.PostToolUse = [];
  const captureHook = {
    type: "command",
    command: `bash ${path.join(hooksDestDir, "capture-session.sh")}`,
  };
  if (!settings.hooks.PostToolUse.some((h) => h.command && h.command.includes("capture-session.sh"))) {
    settings.hooks.PostToolUse.push(captureHook);
  }

  // Stop hook (session end)
  if (!settings.hooks.Stop) settings.hooks.Stop = [];
  const stopHook = {
    type: "command",
    command: `bash ${path.join(hooksDestDir, "session-stop.sh")}`,
  };
  if (!settings.hooks.Stop.some((h) => h.command && h.command.includes("session-stop.sh"))) {
    settings.hooks.Stop.push(stopHook);
  }

  // PreCompact hook (saves context before conversation compression)
  if (!settings.hooks.PreCompact) settings.hooks.PreCompact = [];
  const preCompactHook = {
    type: "command",
    command: `bash ${path.join(hooksDestDir, "pre-compact.sh")}`,
  };
  if (!settings.hooks.PreCompact.some((h) => h.command && h.command.includes("pre-compact.sh"))) {
    settings.hooks.PreCompact.push(preCompactHook);
  }

  const settingsDir = path.dirname(CLAUDE_SETTINGS);
  fs.mkdirSync(settingsDir, { recursive: true });
  fs.writeFileSync(CLAUDE_SETTINGS, JSON.stringify(settings, null, 2));
  log("MCP server + hooks configured in Claude Code settings.");

  // Step 7: Install LaunchAgents (macOS only)
  log("Setting up automated processes...");
  if (platform === "darwin") {
  fs.mkdirSync(LAUNCH_AGENTS, { recursive: true });

  const agents = [
    {
      name: "cognitive-decay",
      script: "nexo-cognitive-decay.py",
      hour: 3,
      minute: 0,
    },
    {
      name: "postmortem",
      script: "nexo-postmortem-consolidator.py",
      hour: 23,
      minute: 30,
    },
    {
      name: "sleep",
      script: "nexo-sleep.py",
      hour: 4,
      minute: 0,
    },
    {
      name: "self-audit",
      script: "nexo-daily-self-audit.py",
      hour: 7,
      minute: 0,
    },
    { name: "catchup", script: "nexo-catchup.py", runAtLoad: true },
  ];

  agents.forEach((agent) => {
    const plistName = `com.nexo.${agent.name}.plist`;
    const plistPath = path.join(LAUNCH_AGENTS, plistName);

    let scheduleBlock = "";
    if (agent.runAtLoad) {
      scheduleBlock = `    <key>RunAtLoad</key>
    <true/>`;
    } else {
      scheduleBlock = `    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${agent.hour}</integer>
        <key>Minute</key>
        <integer>${agent.minute}</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>`;
    }

    const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nexo.${agent.name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${python}</string>
        <string>${path.join(NEXO_HOME, "scripts", agent.script)}</string>
    </array>
    ${scheduleBlock}
    <key>StandardOutPath</key>
    <string>${path.join(NEXO_HOME, "logs", `${agent.name}-stdout.log`)}</string>
    <key>StandardErrorPath</key>
    <string>${path.join(NEXO_HOME, "logs", `${agent.name}-stderr.log`)}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${require("os").homedir()}</string>
        <key>NEXO_HOME</key>
        <string>${NEXO_HOME}</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>`;

    fs.writeFileSync(plistPath, plist);
    // Register the agent
    try {
      execSync(
        `launchctl bootout gui/$(id -u) "${plistPath}" 2>/dev/null; launchctl bootstrap gui/$(id -u) "${plistPath}"`,
        { stdio: "pipe" }
      );
    } catch {
      // May fail if not previously loaded, that's OK
    }
  });
  log(`${agents.length} automated processes configured.`);

  // Caffeinate: keep Mac awake for nocturnal processes
  if (doCaffeinate) {
    const caffHookSrc = path.join(__dirname, "..", "src", "hooks", "caffeinate-guard.sh");
    const caffHookDest = path.join(NEXO_HOME, "hooks", "caffeinate-guard.sh");
    if (fs.existsSync(caffHookSrc)) {
      fs.copyFileSync(caffHookSrc, caffHookDest);
      fs.chmodSync(caffHookDest, "755");
    }

    const caffPlist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nexo.caffeinate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${caffHookDest}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${path.join(NEXO_HOME, "logs", "caffeinate-stdout.log")}</string>
    <key>StandardErrorPath</key>
    <string>${path.join(NEXO_HOME, "logs", "caffeinate-stderr.log")}</string>
</dict>
</plist>`;

    const caffPlistPath = path.join(LAUNCH_AGENTS, "com.nexo.caffeinate.plist");
    fs.writeFileSync(caffPlistPath, caffPlist);
    try {
      execSync(
        `launchctl bootout gui/$(id -u) "${caffPlistPath}" 2>/dev/null; launchctl bootstrap gui/$(id -u) "${caffPlistPath}"`,
        { stdio: "pipe" }
      );
    } catch {}
    log("Caffeinate enabled — Mac will stay awake for cognitive processes.");
  }
  } else {
    log("Non-macOS platform: background tasks will run via catch-up on startup.");
    log("  No OS scheduler configured — NEXO runs maintenance when MCP starts.");
  }

  // Step 8: Create shell alias so user can just type the operator's name
  log("Creating shell alias...");
  const aliasName = operatorName.toLowerCase();
  const aliasLine = `alias ${aliasName}='claude --dangerously-skip-permissions "."'`;
  const aliasComment = `# ${operatorName} — start Claude Code with ${operatorName} speaking first`;

  // Detect shell and add alias
  const userShell = process.env.SHELL || "/bin/bash";
  const rcFile = userShell.includes("zsh")
    ? path.join(require("os").homedir(), ".zshrc")
    : path.join(require("os").homedir(), ".bash_profile");

  let rcContent = "";
  if (fs.existsSync(rcFile)) {
    rcContent = fs.readFileSync(rcFile, "utf8");
  }

  if (!rcContent.includes(`alias ${aliasName}=`)) {
    fs.appendFileSync(rcFile, `\n${aliasComment}\n${aliasLine}\n`);
    log(`Added '${aliasName}' alias to ${path.basename(rcFile)}`);
    log(`After setup, open a new terminal and type: ${aliasName}`);
  } else {
    log(`Alias '${aliasName}' already exists in ${path.basename(rcFile)}`);
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

  console.log("");
  console.log(
    "  ╔══════════════════════════════════════════════════════════╗"
  );
  console.log(
    `  ║  ${operatorName} is ready. Type '${aliasName}' to start.${" ".repeat(Math.max(0, 30 - operatorName.length - aliasName.length))}║`
  );
  console.log(
    "  ╚══════════════════════════════════════════════════════════╝"
  );
  console.log("");

  rl.close();
}

main().catch((err) => {
  console.error("Setup failed:", err.message);
  process.exit(1);
});
