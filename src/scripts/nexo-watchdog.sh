#!/bin/bash
# ============================================================================
# NEXO Watchdog — Health monitor with two-level auto-repair
# ============================================================================
# Monitors all NEXO core LaunchAgents, cron jobs, and infrastructure.
# Level 1: Mechanical repair (launchctl bootstrap/kickstart, chmod)
# Level 2: Launches NEXO CLI for intelligent diagnosis and fix
#
# Install: Add to LaunchAgents for periodic execution (every 5 min recommended)
# ============================================================================
set -uo pipefail

# === PATHS ===
HOME_DIR="$HOME"
NEXO_DIR="$HOME_DIR/claude/nexo-mcp"
OPS_DIR="$HOME_DIR/claude/operations"
LOG_DIR="$HOME_DIR/claude/logs"
LOG="$LOG_DIR/watchdog.log"
STATUS_JSON="$OPS_DIR/watchdog-status.json"
REPORT_TXT="$OPS_DIR/watchdog-report.txt"
ALERT_FILE="$OPS_DIR/.watchdog-alert"
FAIL_COUNT_FILE="$HOME_DIR/claude/scripts/.watchdog-fails"
MAX_FAILS=3

mkdir -p "$LOG_DIR" "$OPS_DIR"

TS=$(date "+%Y-%m-%d %H:%M:%S")
TS_EPOCH=$(date +%s)

log() { echo "[$TS] $1" >> "$LOG"; }

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

UID_NUM=$(id -u)
REPAIR_LOG="$LOG_DIR/watchdog-repairs.log"
TOTAL_HEALED=0

log_repair() { echo "[$TS] REPAIR: $1" >> "$REPAIR_LOG"; log "REPAIR: $1"; }

is_loaded() {
  launchctl list "$1" &>/dev/null
}

file_age() {
  if [ -f "$1" ]; then
    local mod_epoch
    # macOS: stat -f %m, Linux: stat -c %Y
    mod_epoch=$(stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0)
    echo $(( TS_EPOCH - mod_epoch ))
  else
    echo 999999
  fi
}

format_age() {
  local secs=$1
  if [ "$secs" -ge 999999 ]; then
    echo "never"
  elif [ "$secs" -ge 86400 ]; then
    echo "$((secs / 86400))d $((secs % 86400 / 3600))h ago"
  elif [ "$secs" -ge 3600 ]; then
    echo "$((secs / 3600))h $((secs % 3600 / 60))m ago"
  elif [ "$secs" -ge 60 ]; then
    echo "$((secs / 60))m ago"
  else
    echo "${secs}s ago"
  fi
}

check_errors() {
  local logfile="$1"
  if [ -f "$logfile" ] && [ -s "$logfile" ]; then
    tail -50 "$logfile" 2>/dev/null | grep -cE "$ERROR_PATTERNS" 2>/dev/null || echo 0
  else
    echo 0
  fi
}

process_running() {
  if [ -n "$1" ]; then
    pgrep -f "$1" > /dev/null 2>&1
  else
    return 1
  fi
}

json_escape() {
  echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/ /g' | tr '\n' ' '
}

# ============================================================================
# AUTO-REPAIR FUNCTIONS
# ============================================================================

try_repair_launchagent() {
  local plist_id="$1"
  local proc_grep="$2"
  local plist_file="$HOME_DIR/Library/LaunchAgents/${plist_id}.plist"

  # Repair 1: Not loaded — try to bootstrap
  if ! is_loaded "$plist_id"; then
    if [ -f "$plist_file" ]; then
      launchctl bootstrap "gui/$UID_NUM" "$plist_file" 2>/dev/null
      sleep 1
      if is_loaded "$plist_id"; then
        log_repair "$plist_id: bootstrapped successfully"
        return 0
      fi
    fi
    return 1
  fi

  # Repair 2: Loaded but process not running (KeepAlive) — kickstart
  if [ -n "$proc_grep" ] && ! process_running "$proc_grep"; then
    launchctl kickstart "gui/$UID_NUM/$plist_id" 2>/dev/null
    sleep 2
    if process_running "$proc_grep"; then
      log_repair "$plist_id: kickstarted process '$proc_grep'"
      return 0
    fi
  fi

  return 1
}

try_repair_cron() {
  local script="$1"

  if [ -f "$script" ] && [ ! -x "$script" ]; then
    chmod +x "$script"
    if [ -x "$script" ]; then
      log_repair "$script: made executable"
      return 0
    fi
  fi

  return 1
}

try_reexecute_missed_cron() {
  # Re-execute a cron that missed its scheduled run
  local plist_id="$1"
  local plist_file="$HOME_DIR/Library/LaunchAgents/${plist_id}.plist"

  if [ ! -f "$plist_file" ]; then
    return 1
  fi

  local cmd
  cmd=$(python3 -c "
import plistlib, sys
try:
    with open('$plist_file', 'rb') as f:
        d = plistlib.load(f)
    if d.get('KeepAlive'):
        sys.exit(1)
    if not d.get('StartCalendarInterval') and not d.get('StartInterval'):
        sys.exit(1)
    print(' '.join(d.get('ProgramArguments', [])))
except:
    sys.exit(1)
" 2>/dev/null)

  if [ -z "$cmd" ] || [ $? -ne 0 ]; then
    return 1
  fi

  log "Re-executing missed cron: $plist_id"
  timeout 300 bash -c "$cmd" >> "$LOG_DIR/watchdog-reexec.log" 2>&1 &
  local pid=$!
  sleep 2
  if kill -0 "$pid" 2>/dev/null || wait "$pid" 2>/dev/null; then
    log_repair "$plist_id: re-executed missed cron (PID $pid)"
    return 0
  fi
  return 1
}

try_verify_repair() {
  # After Level 2 repair, verify the service is healthy
  local plist_id="$1"
  local log_stdout="$2"
  local proc_grep="$3"
  local max_wait=30

  if ! is_loaded "$plist_id"; then
    return 1
  fi

  if [ -n "$proc_grep" ]; then
    local waited=0
    while [ $waited -lt $max_wait ]; do
      if process_running "$proc_grep"; then
        log "Verify OK: $plist_id process running after ${waited}s"
        return 0
      fi
      sleep 5
      waited=$((waited + 5))
    done
    return 1
  fi

  if [ -n "$log_stdout" ] && [ -f "$log_stdout" ]; then
    local age
    age=$(file_age "$log_stdout")
    if [ "$age" -lt 300 ]; then
      return 0
    fi
  fi

  return 0
}

try_repair_backup() {
  local backup_script="$NEXO_DIR/backup_cron.sh"
  if [ -x "$backup_script" ]; then
    "$backup_script" 2>/dev/null
    sleep 1
    local newest
    newest=$(ls -t "$NEXO_DIR/backups/nexo-"*.db 2>/dev/null | head -1)
    if [ -n "$newest" ]; then
      local age
      age=$(file_age "$newest")
      if [ "$age" -lt 60 ]; then
        log_repair "backup_cron.sh: ran successfully, fresh backup created"
        return 0
      fi
    fi
  fi
  return 1
}

# ============================================================================
# MONITOR REGISTRY — NEXO Core Services
# ============================================================================
# Format: NAME|PLIST_ID|LOG_STDOUT|LOG_STDERR|MAX_STALE_SECS|PROCESS_GREP|SCHEDULE_DESC
#
# Users can add custom monitors in ~/claude/config/watchdog-monitors.conf
# (same format, one per line, # for comments)
# ============================================================================
MONITORS=(
  "Auto-Close Sessions|com.nexo.auto-close-sessions|$HOME_DIR/claude/coordination/auto-close-stdout.log|$HOME_DIR/claude/coordination/auto-close-stderr.log|900||Every 5 min"
  "Catchup|com.nexo.catchup|$HOME_DIR/claude/logs/catchup-stdout.log|$HOME_DIR/claude/logs/catchup-stderr.log|0||RunAtLoad once"
  "Cognitive Decay|com.nexo.cognitive-decay|$HOME_DIR/claude/logs/cognitive-decay-stdout.log|$HOME_DIR/claude/logs/cognitive-decay-stderr.log|90000||Daily 3:00 AM"
  "Evolution|com.nexo.evolution|$HOME_DIR/claude/logs/evolution-stdout.log|$HOME_DIR/claude/logs/evolution-stderr.log|0||Weekly Sun 3:00 AM"
  "GitHub Monitor|com.nexo.github-monitor|$HOME_DIR/claude/logs/github-monitor-stdout.log|$HOME_DIR/claude/logs/github-monitor-stderr.log|90000||Daily 8:00 AM"
  "Immune|com.nexo.immune|$HOME_DIR/claude/coordination/immune-stdout.log|$HOME_DIR/claude/coordination/immune-stderr.log|3600||Every 30 min"
  "Postmortem|com.nexo.postmortem|$HOME_DIR/claude/logs/postmortem-stdout.log|$HOME_DIR/claude/logs/postmortem-stderr.log|90000||Daily 23:30"
  "Prevent Sleep|com.nexo.prevent-sleep|||0|caffeinate|KeepAlive"
  "Self Audit|com.nexo.self-audit|$HOME_DIR/claude/logs/self-audit-stdout.log|$HOME_DIR/claude/logs/self-audit-stderr.log|90000||Daily 7:00 AM"
  "Sleep|com.nexo.sleep|$HOME_DIR/claude/coordination/sleep-stdout.log|$HOME_DIR/claude/coordination/sleep-stderr.log|90000||Daily 4:00 AM"
  "Synthesis|com.nexo.synthesis|$HOME_DIR/claude/coordination/synthesis-stdout.log|$HOME_DIR/claude/coordination/synthesis-stderr.log|10800||Every 2 hours"
)

# Load user-defined monitors if file exists
USER_MONITORS_FILE="$HOME_DIR/claude/config/watchdog-monitors.conf"
if [ -f "$USER_MONITORS_FILE" ]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$line" ]] && continue
    MONITORS+=("$line")
  done < "$USER_MONITORS_FILE"
fi

# Cron jobs to check (NAME|SCRIPT|CHECK_PATH|MAX_STALE_SECS|SCHEDULE)
CRON_MONITORS=(
  "Backup Cron|$NEXO_DIR/backup_cron.sh|$NEXO_DIR/backups/|7200|Hourly"
)

# Error patterns to search in stderr logs (last 50 lines)
ERROR_PATTERNS="Traceback|Error:|CRITICAL|FATAL|ModuleNotFoundError|PermissionError|FileNotFoundError|ConnectionRefused|Errno"

# ============================================================================
# RUN CHECKS
# ============================================================================

TOTAL_PASS=0
TOTAL_WARN=0
TOTAL_FAIL=0
JSON_AGENTS=""
REPORT_LINES=""
FAILED_MONITORS=()  # Track failed monitors for Level 2 repair

for monitor in "${MONITORS[@]}"; do
  [[ "$monitor" =~ ^[[:space:]]*# ]] && continue
  IFS='|' read -r name plist_id log_stdout log_stderr max_stale proc_grep schedule <<< "$monitor"

  status="PASS"
  details=""
  loaded="unknown"
  stale_age="n/a"
  error_count=0
  proc_alive="n/a"

  # Check 1: LaunchAgent loaded?
  if is_loaded "$plist_id"; then
    loaded="yes"
  else
    loaded="no"
    if try_repair_launchagent "$plist_id" "$proc_grep"; then
      loaded="yes"
      status="HEALED"
      details="${details}Self-healed: bootstrapped. "
      TOTAL_HEALED=$((TOTAL_HEALED + 1))
    else
      status="FAIL"
      details="${details}Not loaded in launchctl (repair failed). "
    fi
  fi

  # Check 2: Process alive? (only for KeepAlive / long-running)
  if [ -n "$proc_grep" ]; then
    if process_running "$proc_grep"; then
      proc_alive="yes"
    else
      proc_alive="no"
      if [ "$status" != "FAIL" ] && [ "$status" != "HEALED" ]; then
        if try_repair_launchagent "$plist_id" "$proc_grep"; then
          proc_alive="yes"
          status="HEALED"
          details="${details}Self-healed: kickstarted. "
          TOTAL_HEALED=$((TOTAL_HEALED + 1))
        else
          status="WARN"
          details="${details}Process '$proc_grep' not running (repair failed). "
        fi
      elif [ "$status" = "HEALED" ]; then
        sleep 1
        if process_running "$proc_grep"; then
          proc_alive="yes"
        else
          details="${details}Process '$proc_grep' still not running after bootstrap. "
        fi
      fi
    fi
  fi

  # Check 3: Log staleness + AUTO RE-EXECUTE missed crons
  if [ -n "$log_stdout" ] && [ "$max_stale" -gt 0 ]; then
    age=$(file_age "$log_stdout")
    stale_age=$(format_age "$age")
    if [ "$age" -gt $(( max_stale * 3 )) ]; then
      if try_reexecute_missed_cron "$plist_id"; then
        status="HEALED"
        details="${details}Self-healed: re-executed missed cron (was stale: $stale_age). "
        TOTAL_HEALED=$((TOTAL_HEALED + 1))
      else
        status="FAIL"
        details="${details}Log stale: $stale_age (limit: $(format_age "$max_stale")). Re-execute failed. "
      fi
    elif [ "$age" -gt "$max_stale" ]; then
      [ "$status" = "PASS" ] && status="WARN"
      details="${details}Log slightly stale: $stale_age. "
    fi
  elif [ -n "$log_stdout" ]; then
    if [ -f "$log_stdout" ]; then
      age=$(file_age "$log_stdout")
      stale_age=$(format_age "$age")
    else
      stale_age="no log file"
    fi
  fi

  # Check 4: Errors in stderr log
  if [ -n "$log_stderr" ]; then
    error_count=$(check_errors "$log_stderr")
    if [ "$error_count" -gt 5 ]; then
      [ "$status" = "PASS" ] && status="WARN"
      details="${details}${error_count} errors in recent stderr. "
    fi
  fi

  [ -z "$details" ] && details="All checks passed"

  case "$status" in
    PASS|HEALED) TOTAL_PASS=$((TOTAL_PASS + 1)) ;;
    WARN) TOTAL_WARN=$((TOTAL_WARN + 1)) ;;
    FAIL)
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
      FAILED_MONITORS+=("${name}|${plist_id}|${log_stdout}|${log_stderr}|${proc_grep}|${schedule}|${details}")
      ;;
  esac

  # JSON
  escaped_details=$(json_escape "$details")
  json_item="    {\"name\":\"$name\",\"plist\":\"$plist_id\",\"status\":\"$status\",\"loaded\":\"$loaded\",\"process\":\"$proc_alive\",\"last_activity\":\"$stale_age\",\"stderr_errors\":$error_count,\"schedule\":\"$schedule\",\"details\":\"$escaped_details\"}"
  [ -n "$JSON_AGENTS" ] && JSON_AGENTS="${JSON_AGENTS},
${json_item}" || JSON_AGENTS="$json_item"

  # Report
  case "$status" in
    PASS) icon="PASS" ;; HEALED) icon="HEAL" ;; WARN) icon="WARN" ;; FAIL) icon="FAIL" ;; *) icon="????" ;;
  esac
  REPORT_LINES="${REPORT_LINES}  [${icon}] ${name} (${schedule})
         Loaded: ${loaded} | Process: ${proc_alive} | Last: ${stale_age} | Errors: ${error_count}
         ${details}
"
done

# --- Cron job checks ---
CRON_JSON=""
CRON_REPORT=""
for cron_entry in "${CRON_MONITORS[@]}"; do
  IFS='|' read -r name script check_path max_stale schedule <<< "$cron_entry"

  c_status="PASS"
  c_details=""
  age_str="n/a"

  if [ ! -x "$script" ]; then
    if try_repair_cron "$script"; then
      c_status="HEALED"
      c_details="Self-healed: made executable. "
      TOTAL_HEALED=$((TOTAL_HEALED + 1))
    else
      c_status="FAIL"
      c_details="Script not executable or missing (repair failed). "
    fi
  fi

  if [ -d "$check_path" ]; then
    newest=$(ls -t "$check_path" 2>/dev/null | head -1)
    if [ -n "$newest" ]; then
      age=$(file_age "${check_path}${newest}")
      age_str=$(format_age "$age")
      if [ "$age" -gt $(( max_stale * 3 )) ]; then
        c_status="FAIL"
        c_details="${c_details}Output stale: $age_str. "
      elif [ "$age" -gt "$max_stale" ]; then
        [ "$c_status" = "PASS" ] && c_status="WARN"
        c_details="${c_details}Output slightly stale: $age_str. "
      fi
    else
      c_status="WARN"
      c_details="${c_details}No output files found. "
      age_str="no files"
    fi
  elif [ -f "$check_path" ]; then
    age=$(file_age "$check_path")
    age_str=$(format_age "$age")
    if [ "$age" -gt $(( max_stale * 3 )) ]; then
      c_status="FAIL"
      c_details="${c_details}Output stale: $age_str. "
    elif [ "$age" -gt "$max_stale" ]; then
      [ "$c_status" = "PASS" ] && c_status="WARN"
      c_details="${c_details}Output slightly stale: $age_str. "
    fi
  fi

  [ -z "$c_details" ] && c_details="All checks passed"

  case "$c_status" in
    PASS|HEALED) TOTAL_PASS=$((TOTAL_PASS + 1)) ;;
    WARN) TOTAL_WARN=$((TOTAL_WARN + 1)) ;;
    FAIL) TOTAL_FAIL=$((TOTAL_FAIL + 1)) ;;
  esac

  escaped_details=$(json_escape "$c_details")
  cron_item="    {\"name\":\"$name\",\"script\":\"$script\",\"status\":\"$c_status\",\"last_output\":\"$age_str\",\"schedule\":\"$schedule\",\"details\":\"$escaped_details\"}"
  [ -n "$CRON_JSON" ] && CRON_JSON="${CRON_JSON},
${cron_item}" || CRON_JSON="$cron_item"

  case "$c_status" in
    PASS) icon="PASS" ;; HEALED) icon="HEAL" ;; WARN) icon="WARN" ;; FAIL) icon="FAIL" ;; *) icon="????" ;;
  esac
  CRON_REPORT="${CRON_REPORT}  [${icon}] ${name} (${schedule})
         Last output: ${age_str}
         ${c_details}
"
done

# ============================================================================
# INFRASTRUCTURE CHECKS
# ============================================================================

# --- SQLite integrity ---
SQLITE_STATUS="PASS"
SQLITE_DETAIL=""
INTEGRITY=$(sqlite3 "$NEXO_DIR/nexo.db" "PRAGMA integrity_check;" 2>/dev/null || echo "CORRUPT")
if [ "$INTEGRITY" != "ok" ]; then
  SQLITE_STATUS="FAIL"
  SQLITE_DETAIL="Integrity check: $INTEGRITY"
  log "CRITICAL: SQLite integrity check failed: $INTEGRITY"
  TOTAL_FAIL=$((TOTAL_FAIL + 1))
  # Save corrupt copy before restoring
  cp "$NEXO_DIR/nexo.db" "$NEXO_DIR/nexo.db.corrupt.$(date +%s)" 2>/dev/null
  LATEST_BACKUP=$(ls -t "$NEXO_DIR/backups/nexo-"*.db 2>/dev/null | head -1)
  if [ -n "$LATEST_BACKUP" ]; then
    cp "$LATEST_BACKUP" "$NEXO_DIR/nexo.db"
    log "RESTORED from $LATEST_BACKUP"
    SQLITE_DETAIL="${SQLITE_DETAIL}. Restored from backup."
  fi
else
  SQLITE_DETAIL="Integrity OK"
  TOTAL_PASS=$((TOTAL_PASS + 1))
fi

# --- Cognitive DB check ---
COG_STATUS="PASS"
COG_DETAIL=""
COG_DB="$NEXO_DIR/cognitive.db"
if [ -f "$COG_DB" ]; then
  COG_INT=$(sqlite3 "$COG_DB" "PRAGMA integrity_check;" 2>/dev/null || echo "CORRUPT")
  if [ "$COG_INT" != "ok" ]; then
    COG_STATUS="FAIL"
    COG_DETAIL="Cognitive DB integrity: $COG_INT"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
  else
    COG_DETAIL="Integrity OK"
    TOTAL_PASS=$((TOTAL_PASS + 1))
  fi
else
  COG_STATUS="WARN"
  COG_DETAIL="cognitive.db not found"
  TOTAL_WARN=$((TOTAL_WARN + 1))
fi

# --- Backup freshness ---
BACKUP_STATUS="PASS"
BACKUP_DETAIL=""
LATEST_BACKUP=$(ls -t "$NEXO_DIR/backups/nexo-"*.db 2>/dev/null | head -1)
if [ -n "$LATEST_BACKUP" ]; then
  BACKUP_AGE=$(file_age "$LATEST_BACKUP")
  BACKUP_AGE_STR=$(format_age "$BACKUP_AGE")
  if [ "$BACKUP_AGE" -gt 7200 ]; then
    if try_repair_backup; then
      BACKUP_STATUS="HEALED"
      BACKUP_DETAIL="Self-healed: backup was stale ($BACKUP_AGE_STR), ran fresh backup"
      TOTAL_HEALED=$((TOTAL_HEALED + 1))
      TOTAL_PASS=$((TOTAL_PASS + 1))
    else
      BACKUP_STATUS="WARN"
      BACKUP_DETAIL="Last backup: $BACKUP_AGE_STR (>2h, repair failed)"
      TOTAL_WARN=$((TOTAL_WARN + 1))
    fi
  else
    BACKUP_DETAIL="Last backup: $BACKUP_AGE_STR"
    TOTAL_PASS=$((TOTAL_PASS + 1))
  fi
else
  BACKUP_STATUS="FAIL"
  BACKUP_DETAIL="No backups found"
  TOTAL_FAIL=$((TOTAL_FAIL + 1))
fi

# ============================================================================
# WRITE JSON STATUS
# ============================================================================
TOTAL=$((TOTAL_PASS + TOTAL_WARN + TOTAL_FAIL))
OVERALL="PASS"
[ "$TOTAL_WARN" -gt 0 ] && OVERALL="WARN"
[ "$TOTAL_FAIL" -gt 0 ] && OVERALL="FAIL"

cat > "$STATUS_JSON" <<JSONEOF
{
  "timestamp": "$TS",
  "summary": {
    "total": $TOTAL,
    "pass": $TOTAL_PASS,
    "warn": $TOTAL_WARN,
    "fail": $TOTAL_FAIL,
    "healed": $TOTAL_HEALED,
    "overall": "$OVERALL"
  },
  "launch_agents": [
$JSON_AGENTS
  ],
  "cron_jobs": [
$CRON_JSON
  ],
  "infrastructure": {
    "sqlite": {"status": "$SQLITE_STATUS", "detail": "$(json_escape "$SQLITE_DETAIL")"},
    "cognitive_db": {"status": "$COG_STATUS", "detail": "$(json_escape "$COG_DETAIL")"},
    "backups": {"status": "$BACKUP_STATUS", "detail": "$(json_escape "$BACKUP_DETAIL")"}
  }
}
JSONEOF

# ============================================================================
# WRITE HUMAN-READABLE REPORT
# ============================================================================
cat > "$REPORT_TXT" <<REPORTEOF
======================================================
  NEXO WATCHDOG REPORT — $TS
======================================================
  PASS: $TOTAL_PASS  |  HEALED: $TOTAL_HEALED  |  WARN: $TOTAL_WARN  |  FAIL: $TOTAL_FAIL  |  TOTAL: $TOTAL
  OVERALL: $OVERALL
======================================================

-- LaunchAgents (${#MONITORS[@]}) ---------------------
$REPORT_LINES
-- Cron Jobs ------------------------------------------
$CRON_REPORT
-- Infrastructure -------------------------------------
  [$SQLITE_STATUS] SQLite nexo.db: $SQLITE_DETAIL
  [$COG_STATUS] Cognitive DB: $COG_DETAIL
  [$BACKUP_STATUS] Backups: $BACKUP_DETAIL

-- End of Report --------------------------------------
REPORTEOF

# ============================================================================
# ALERT FILE
# ============================================================================
if [ "$TOTAL_FAIL" -gt 0 ]; then
  {
    echo "timestamp=$TS"
    echo "fail_count=$TOTAL_FAIL"
    echo "warn_count=$TOTAL_WARN"
    echo "failures:"
    grep '\[FAIL\]' "$REPORT_TXT" | head -10 | sed 's/^/  /'
  } > "$ALERT_FILE"
  log "ALERT: $TOTAL_FAIL failures detected"
else
  rm -f "$ALERT_FILE"
fi

# ============================================================================
# CONSECUTIVE FAILURE TRACKING
# ============================================================================
FAILS=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)
if [ "$TOTAL_FAIL" -gt 0 ]; then
  FAILS=$((FAILS + 1))
  echo "$FAILS" > "$FAIL_COUNT_FILE"
  if [ "$FAILS" -ge "$MAX_FAILS" ]; then
    log "ALERT: $FAILS consecutive runs with failures"
  fi
else
  echo "0" > "$FAIL_COUNT_FILE"
fi

# ============================================================================
# LEVEL 2 AUTO-REPAIR: Launch NEXO for intelligent diagnosis
# ============================================================================
REPAIR_LOCK="$HOME_DIR/claude/scripts/.watchdog-nexo-repair.lock"
REPAIR_COOLDOWN=1800  # 30 min between NEXO repair attempts

if [ "$TOTAL_FAIL" -gt 0 ]; then
  LOCK_AGE=999999
  SKIP_REPAIR=false
  if [ -f "$REPAIR_LOCK" ]; then
    LOCK_AGE=$(file_age "$REPAIR_LOCK")
    if [ "$LOCK_AGE" -lt "$REPAIR_COOLDOWN" ]; then
      log "NEXO repair skipped: cooldown (${LOCK_AGE}s < ${REPAIR_COOLDOWN}s)"
      SKIP_REPAIR=true
    fi
  fi

  if ! $SKIP_REPAIR; then
    # Collect failure details from tracked FAILED_MONITORS array
    FAIL_DETAILS=""
    for failed in "${FAILED_MONITORS[@]}"; do
      IFS='|' read -r m_name m_plist m_stdout m_stderr m_proc m_sched m_details <<< "$failed"
      STDERR_TAIL=""
      if [ -n "$m_stderr" ] && [ -f "$m_stderr" ]; then
        STDERR_TAIL=$(tail -20 "$m_stderr" 2>/dev/null | head -20)
      fi
      STDOUT_TAIL=""
      if [ -n "$m_stdout" ] && [ -f "$m_stdout" ]; then
        STDOUT_TAIL=$(tail -10 "$m_stdout" 2>/dev/null | head -10)
      fi
      FAIL_DETAILS="${FAIL_DETAILS}
--- ${m_name} (${m_plist}) ---
Schedule: ${m_sched}
Failure reason: ${m_details}
Plist: ~/Library/LaunchAgents/${m_plist}.plist
Process grep: ${m_proc}
Stderr (last 20 lines):
${STDERR_TAIL}
Stdout (last 10 lines):
${STDOUT_TAIL}
"
    done

    if [ -n "$FAIL_DETAILS" ]; then
      touch "$REPAIR_LOCK"
      log "Launching NEXO Level 2 repair..."

      REPAIR_PROMPT_FILE=$(mktemp /tmp/nexo-repair-XXXXXX.txt)
      cat > "$REPAIR_PROMPT_FILE" <<NEXOPROMPT
WATCHDOG LEVEL 2 REPAIR — Automated diagnostic session.

The watchdog detected services that failed mechanical repair (launchctl bootstrap/kickstart).
Your job: diagnose the root cause and fix it. Do NOT ask the user anything — resolve autonomously.

FAILURES:
${FAIL_DETAILS}

STEPS:
1. Read the plist file to understand the service configuration
2. Check stderr/stdout logs for the actual error
3. Fix the root cause (missing file, bad config, dependency issue, etc.)
4. Reload the service and verify it is running
5. Log what you did to ~/claude/logs/watchdog-repair-result.log

CONSTRAINTS:
- Do NOT modify CLAUDE.md or any protected file
- Do NOT start interactive conversations
- Keep it under 5 minutes
- Log what you did to ~/claude/logs/watchdog-repair-result.log
NEXOPROMPT

      # Find claude CLI (may not be in PATH for cron/LaunchAgent)
      CLAUDE_BIN=$(command -v claude 2>/dev/null || echo "$HOME_DIR/.claude/local/bin/claude")
      if [ ! -x "$CLAUDE_BIN" ]; then
        CLAUDE_BIN=$(find /usr/local/bin /opt/homebrew/bin "$HOME_DIR/.local/bin" "$HOME_DIR/.npm-global/bin" -name claude -type f 2>/dev/null | head -1)
      fi

      if [ -n "$CLAUDE_BIN" ] && [ -x "$CLAUDE_BIN" ]; then
        nohup bash -c "\"$CLAUDE_BIN\" --print --dangerously-skip-permissions -p \"\$(cat '$REPAIR_PROMPT_FILE')\" >> '$LOG_DIR/watchdog-nexo-repair.log' 2>&1; rm -f '$REPAIR_PROMPT_FILE'" &
        log "NEXO repair launched (PID: $!)"
      else
        log "NEXO repair ABORTED: claude CLI not found in PATH"
        rm -f "$REPAIR_PROMPT_FILE"
      fi
    fi
  fi
fi

# ============================================================================
# LOG SUMMARY
# ============================================================================
log "Complete: PASS=$TOTAL_PASS HEALED=$TOTAL_HEALED WARN=$TOTAL_WARN FAIL=$TOTAL_FAIL"
