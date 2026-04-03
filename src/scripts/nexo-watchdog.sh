#!/bin/bash
# ============================================================================
# NEXO Watchdog — Comprehensive health monitor for all NEXO services
# Schedule: every 30 minutes (interval_seconds: 1800)
# ============================================================================
# Monitors ALL LaunchAgents, cron jobs, and background processes.
# Outputs: watchdog-status.json (machine), watchdog-report.txt (human),
#          .watchdog-alert (if any FAIL detected)
# ============================================================================
set -uo pipefail

# === PATHS ===
HOME_DIR="$HOME"
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_DIR="$NEXO_HOME"
CORTEX_DIR="$NEXO_HOME/brain"
OPS_DIR="$NEXO_HOME/operations"
LOG_DIR="$NEXO_HOME/logs"
DB_PATH="$NEXO_HOME/data/nexo.db"
LOG="$LOG_DIR/watchdog.log"
STATUS_JSON="$OPS_DIR/watchdog-status.json"
REPORT_TXT="$OPS_DIR/watchdog-report.txt"
ALERT_FILE="$OPS_DIR/.watchdog-alert"
HASH_REGISTRY="$NEXO_HOME/scripts/.watchdog-hashes"
FAIL_COUNT_FILE="$NEXO_HOME/scripts/.watchdog-fails"
MAX_FAILS=3

mkdir -p "$LOG_DIR" "$OPS_DIR"

TS=$(date "+%Y-%m-%d %H:%M:%S")
TS_EPOCH=$(date +%s)

log() { echo "[$TS] $1" >> "$LOG"; }

# ============================================================================
# MONITOR REGISTRY — generated dynamically from manifest.json
# ============================================================================
# Format: NAME|PLIST_ID|LOG_STDOUT|LOG_STDERR|MAX_STALE_SECS|PROCESS_GREP|SCHEDULE_DESC|TYPE
#
# MAX_STALE_SECS: how old stdout log can be before WARN.
#   0 = skip staleness check (for one-shot or infrequent tasks)
#   WARN at MAX_STALE_SECS, FAIL at 3x MAX_STALE_SECS
# PROCESS_GREP: pattern to grep in ps (empty = skip process check)
# ============================================================================
# Core monitors are built from crons/manifest.json (single source of truth).
# The NEXO_CODE env var must point to the repo src/ directory.
# Add personal (non-manifest) monitors to PERSONAL_MONITORS below.
NEXO_CODE="${NEXO_CODE:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
# Look for manifest in NEXO_HOME first (packaged install), then NEXO_CODE (dev/repo)
if [ -f "$NEXO_HOME/crons/manifest.json" ]; then
  MANIFEST_FILE="$NEXO_HOME/crons/manifest.json"
else
  MANIFEST_FILE="$NEXO_CODE/crons/manifest.json"
fi

_build_monitors_from_manifest() {
  if [ ! -f "$MANIFEST_FILE" ]; then
    log "WARNING: manifest.json not found at $MANIFEST_FILE — no core monitors loaded"
    return
  fi
  python3 -c "
import json, sys, platform

nexo_home = '$NEXO_HOME'
is_mac = platform.system() == 'Darwin'

with open('$MANIFEST_FILE') as f:
    data = json.load(f)

for c in data.get('crons', []):
    cid = c['id']
    name = cid.replace('-', ' ').title()
    # Use the right service identifier per platform
    if is_mac:
        svc_id = 'com.nexo.' + cid
    else:
        svc_id = 'nexo-' + cid + '.timer'
    stdout_log = nexo_home + '/logs/' + cid + '-stdout.log'
    stderr_log = nexo_home + '/logs/' + cid + '-stderr.log'

    # Derive max_stale_secs and schedule_desc from schedule config
    if c.get('run_at_load'):
        max_stale = 0
        schedule_desc = 'RunAtLoad once'
    elif 'interval_seconds' in c:
        iv = c['interval_seconds']
        # Allow 2x the interval before WARN
        max_stale = iv * 2
        if iv >= 3600:
            schedule_desc = f'Every {iv // 3600}h'
        else:
            schedule_desc = f'Every {iv // 60} min'
    elif 'schedule' in c:
        s = c['schedule']
        h = s.get('hour', 0)
        m = s.get('minute', 0)
        if 'weekday' in s:
            days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
            schedule_desc = f'Weekly {days[s[\"weekday\"]]} {h}:{m:02d}'
            max_stale = 0  # weekly tasks: skip staleness
        else:
            schedule_desc = f'Daily {h}:{m:02d}'
            max_stale = 90000  # ~25h
    else:
        max_stale = 0
        schedule_desc = 'unknown'

    mon_type = 'core' if c.get('core') else 'personal'
    proc_grep = ''  # manifest crons are one-shot, no persistent process

    print(f'{name}|{svc_id}|{stdout_log}|{stderr_log}|{max_stale}|{proc_grep}|{schedule_desc}|{mon_type}')
" 2>/dev/null
}

MONITORS=()
while IFS= read -r line; do
  [ -n "$line" ] && MONITORS+=("$line")
done < <(_build_monitors_from_manifest)

# Personal (non-manifest) monitors — add yours below.
# These are NOT in manifest.json and won't be synced by cron-sync.
PERSONAL_MONITORS=(
  # "My Service|com.nexo.my-service|$NEXO_HOME/logs/my-service.log||3600||Every 30 min|personal"
)
MONITORS+=("${PERSONAL_MONITORS[@]+"${PERSONAL_MONITORS[@]}"}")

# Cron jobs to check (NAME|SCRIPT|CHECK_PATH|MAX_STALE_SECS|SCHEDULE)
# Core cron monitors are loaded from manifest above.
# Maintainer-only monitors go here (guarded by NEXO_MAINTAINER env var).
CRON_MONITORS=()
if [ "${NEXO_MAINTAINER:-}" = "1" ]; then
  CRON_MONITORS+=(
    "Backup|$NEXO_DIR/scripts/nexo-backup.sh|$NEXO_DIR/backups/|7200|Hourly"
  )
fi

# Error patterns to search in stderr logs (last 50 lines)
ERROR_PATTERNS="Traceback|Error:|CRITICAL|FATAL|ModuleNotFoundError|PermissionError|FileNotFoundError|ConnectionRefused|Errno|Operation not permitted|SyntaxError|sqlite3\\.OperationalError"

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

UID_NUM=$(id -u)
REPAIR_LOG="$LOG_DIR/watchdog-repairs.log"
TOTAL_HEALED=0
IS_MACOS=false
[ "$(uname)" = "Darwin" ] && IS_MACOS=true

log_repair() { echo "[$TS] REPAIR: $1" >> "$REPAIR_LOG"; log "REPAIR: $1"; }

is_loaded() {
  if $IS_MACOS; then
    launchctl print "gui/$UID_NUM/$1" &>/dev/null
  else
    # On Linux, check if the systemd timer is enabled
    systemctl --user is-enabled "$1" &>/dev/null
  fi
}

# ============================================================================
# AUTO-REPAIR FUNCTIONS
# ============================================================================

try_repair_launchagent() {
  $IS_MACOS || return 1
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

try_repair_systemd() {
  $IS_MACOS && return 1
  local timer_unit="$1"
  local service_unit="${timer_unit%.timer}.service"

  # Repair 1: Timer not enabled — try to enable and start
  if ! systemctl --user is-enabled "$timer_unit" &>/dev/null; then
    systemctl --user daemon-reload 2>/dev/null
    systemctl --user enable --now "$timer_unit" 2>/dev/null
    sleep 1
    if systemctl --user is-enabled "$timer_unit" &>/dev/null; then
      log_repair "$timer_unit: enabled and started"
      return 0
    fi
    return 1
  fi

  # Repair 2: Timer enabled but not active — start it
  if ! systemctl --user is-active "$timer_unit" &>/dev/null; then
    systemctl --user start "$timer_unit" 2>/dev/null
    sleep 1
    if systemctl --user is-active "$timer_unit" &>/dev/null; then
      log_repair "$timer_unit: restarted"
      return 0
    fi
  fi

  return 1
}

try_repair_cron() {
  local script="$1"

  # Repair: Script not executable — chmod it
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
  local svc_id="$1"

  if $IS_MACOS; then
    log "Re-executing missed cron via launchctl kickstart: $svc_id"
    if launchctl kickstart -k "gui/$UID_NUM/$svc_id" >> "$LOG_DIR/watchdog-reexec.log" 2>&1; then
      log_repair "$svc_id: re-executed missed cron via launchctl kickstart"
      return 0
    fi
    log "Re-execute failed for $svc_id"
    return 1
  else
    # Linux: start the corresponding service unit directly
    local service_unit="${svc_id%.timer}.service"
    log "Re-executing missed cron: $svc_id → systemctl start $service_unit"
    if systemctl --user start "$service_unit" 2>/dev/null; then
      log_repair "$svc_id: re-executed via systemctl start $service_unit"
      return 0
    else
      log "Re-execute failed for $svc_id"
      return 1
    fi
  fi
}

try_verify_repair() {
  # After Level 2 repair, wait and verify the service is healthy
  local plist_id="$1"
  local log_stdout="$2"
  local proc_grep="$3"
  local mon_type="${4:-core}"
  local max_wait=30

  log "Verifying repair for $plist_id..."

  # Check 1: Is it loaded?
  if ! is_loaded "$plist_id"; then
    log "Verify FAILED: $plist_id still not loaded"
    return 1
  fi

  # Check 2: Process running? (for KeepAlive services)
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
    log "Verify FAILED: $plist_id process not running after ${max_wait}s"
    return 1
  fi

  # Check 3: For scheduled crons, check if cron_runs/logs were updated recently
  if [ "$mon_type" = "core" ]; then
    local cron_id
    cron_id=$(cron_id_from_service "$plist_id")
    local run_info
    run_info=$(cron_last_run_info "$cron_id" || true)
    if [ -n "$run_info" ]; then
      local run_age
      IFS='|' read -r run_age _ _ _ _ _ <<< "$run_info"
      if [ -n "$run_age" ] && [ "$run_age" -lt 300 ]; then
        log "Verify OK: $plist_id cron_runs updated ${run_age}s ago"
        return 0
      fi
    fi
  fi

  if [ -n "$log_stdout" ] && [ -f "$log_stdout" ]; then
    local age
    age=$(file_age "$log_stdout")
    if [ "$age" -lt 300 ]; then
      log "Verify OK: $plist_id log updated ${age}s ago"
      return 0
    fi
  fi

  # If we get here for a scheduled service, it's loaded which is sufficient
  log "Verify OK: $plist_id is loaded (scheduled service)"
  return 0
}

try_repair_backup() {
  # Use the core backup script (nexo-backup.sh)
  local backup_script="$NEXO_DIR/scripts/nexo-backup.sh"
  [ ! -x "$backup_script" ] && backup_script="$SCRIPT_DIR/nexo-backup.sh"
  if [ -x "$backup_script" ]; then
    bash "$backup_script" 2>/dev/null
    sleep 1
    local newest
    newest=$(ls -t "$NEXO_DIR/backups/nexo-"*.db 2>/dev/null | head -1)
    if [ -n "$newest" ]; then
      if $IS_MACOS; then local age=$(( TS_EPOCH - $(stat -f %m "$newest") )); else local age=$(( TS_EPOCH - $(stat -c %Y "$newest") )); fi
      if [ "$age" -lt 60 ]; then
        log_repair "nexo-backup.sh: ran successfully, fresh backup created"
        return 0
      fi
    fi
  fi
  return 1
}

file_age() {
  if [ -f "$1" ]; then
    local mod_epoch
    if $IS_MACOS; then
      mod_epoch=$(stat -f %m "$1" 2>/dev/null || echo 0)
    else
      mod_epoch=$(stat -c %Y "$1" 2>/dev/null || echo 0)
    fi
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
    local count
    count=$(tail -50 "$logfile" 2>/dev/null | grep -cE "$ERROR_PATTERNS" 2>/dev/null) || true
    echo "${count:-0}"
  else
    echo 0
  fi
}

process_running() {
  if [ -n "$1" ]; then
    pgrep -f "$1" > /dev/null 2>&1
  else
    return 0
  fi
}

cron_id_from_service() {
  local svc_id="$1"
  if $IS_MACOS; then
    echo "${svc_id#com.nexo.}"
  else
    echo "${svc_id#nexo-}" | sed 's/\.timer$//'
  fi
}

cron_last_run_info() {
  local cron_id="$1"
  [ ! -f "$DB_PATH" ] && return 1
  sqlite3 -separator '|' "$DB_PATH" "
    SELECT
      CAST(strftime('%s','now') - strftime('%s', started_at) AS INTEGER) AS age_secs,
      COALESCE(started_at, ''),
      COALESCE(ended_at, ''),
      COALESCE(exit_code, ''),
      COALESCE(error, ''),
      COALESCE(summary, '')
    FROM cron_runs
    WHERE cron_id = '$cron_id'
    ORDER BY id DESC
    LIMIT 1;
  " 2>/dev/null
}

classify_log_issue() {
  local logfile="$1"
  if [ ! -f "$logfile" ] || [ ! -s "$logfile" ]; then
    return 0
  fi
  local tail_text
  tail_text=$(tail -50 "$logfile" 2>/dev/null || true)
  if echo "$tail_text" | grep -q "Operation not permitted"; then
    echo "tcc"
  elif echo "$tail_text" | grep -q "ModuleNotFoundError"; then
    echo "dependency"
  elif echo "$tail_text" | grep -q "SyntaxError"; then
    echo "syntax"
  elif echo "$tail_text" | grep -q "sqlite3.OperationalError"; then
    echo "schema"
  fi
}

# Escape strings for JSON
json_escape() {
  echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/ /g' | tr '\n' ' '
}

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
  # Skip comment lines
  [[ "$monitor" =~ ^[[:space:]]*# ]] && continue
  IFS='|' read -r name plist_id log_stdout log_stderr max_stale proc_grep schedule mon_type <<< "$monitor"
  mon_type="${mon_type:-core}"

  status="PASS"
  details=""
  loaded="unknown"
  stale_age="n/a"
  error_count=0
  proc_alive="n/a"
  error_kind=""
  cron_id=$(cron_id_from_service "$plist_id")
  latest_run_has_record=false
  latest_run_failed=false

  # Check 1: Service loaded? (launchd on macOS, systemd on Linux)
  if is_loaded "$plist_id"; then
    loaded="yes"
  else
    loaded="no"
    # AUTO-REPAIR: try platform-appropriate repair
    repair_ok=false
    if $IS_MACOS; then
      try_repair_launchagent "$plist_id" "$proc_grep" && repair_ok=true
    else
      try_repair_systemd "$plist_id" && repair_ok=true
    fi
    if $repair_ok; then
      loaded="yes"
      status="HEALED"
      details="${details}Self-healed: service re-registered. "
      TOTAL_HEALED=$((TOTAL_HEALED + 1))
    else
      status="FAIL"
      details="${details}Service not loaded (repair failed). "
    fi
  fi

  # Check 2: Process alive? (only for KeepAlive / long-running)
  if [ -n "$proc_grep" ]; then
    if process_running "$proc_grep"; then
      proc_alive="yes"
    else
      proc_alive="no"
      # AUTO-REPAIR: try to kickstart (platform-appropriate)
      if [ "$status" != "FAIL" ] && [ "$status" != "HEALED" ]; then
        if ($IS_MACOS && try_repair_launchagent "$plist_id" "$proc_grep") || \
           (! $IS_MACOS && try_repair_systemd "$plist_id"); then
          proc_alive="yes"
          status="HEALED"
          details="${details}Self-healed: kickstarted. "
          TOTAL_HEALED=$((TOTAL_HEALED + 1))
        else
          status="WARN"
          details="${details}Process '$proc_grep' not running (repair failed). "
        fi
      elif [ "$status" = "HEALED" ]; then
        # Already healed by bootstrap, check if process came up
        sleep 1
        if process_running "$proc_grep"; then
          proc_alive="yes"
        else
          details="${details}Process '$proc_grep' still not running after bootstrap. "
        fi
      fi
    fi
  fi

  # Check 3: Staleness + AUTO RE-EXECUTE missed crons
  if [ "$mon_type" = "core" ] && [ "$max_stale" -gt 0 ]; then
    run_info=$(cron_last_run_info "$cron_id" || true)
    if [ -n "$run_info" ]; then
      latest_run_has_record=true
      IFS='|' read -r age _ _ last_exit last_error last_summary <<< "$run_info"
      age="${age:-999999}"
      stale_age=$(format_age "$age")
      if [ -n "$last_exit" ] && [ "$last_exit" != "0" ]; then
        latest_run_failed=true
        status="FAIL"
        details="${details}Last run exited ${last_exit}. "
        [ -n "$last_error" ] && details="${details}Error: ${last_error}. "
      fi
      if [ "$age" -gt $(( max_stale * 3 )) ]; then
        if try_reexecute_missed_cron "$plist_id"; then
          status="HEALED"
          details="${details}Self-healed: re-executed missed cron (last run: $stale_age). "
          TOTAL_HEALED=$((TOTAL_HEALED + 1))
        else
          status="FAIL"
          details="${details}cron_runs stale: $stale_age (limit: $(format_age "$max_stale")). Re-execute failed. "
        fi
      elif [ "$age" -gt "$max_stale" ]; then
        [ "$status" = "PASS" ] && status="WARN"
        details="${details}cron_runs slightly stale: $stale_age. "
      elif [ -z "$details" ] && [ -n "$last_summary" ]; then
        details="${details}Last run summary: ${last_summary}. "
      fi
    else
      stale_age="no cron_runs entry"
      if try_reexecute_missed_cron "$plist_id"; then
        status="HEALED"
        details="${details}Self-healed: executed missing cron for first run. "
        TOTAL_HEALED=$((TOTAL_HEALED + 1))
      else
        status="FAIL"
        details="${details}No cron_runs entry recorded yet. "
      fi
    fi
  elif [ -n "$log_stdout" ] && [ "$max_stale" -gt 0 ]; then
    age=$(file_age "$log_stdout")
    stale_age=$(format_age "$age")
    if [ "$age" -gt $(( max_stale * 3 )) ]; then
      # Severely stale — try to re-execute the missed cron
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
    consider_stderr=true
    if [ "$mon_type" = "core" ] && $latest_run_has_record && ! $latest_run_failed && [ "$loaded" = "yes" ]; then
      consider_stderr=false
    fi
    if $consider_stderr; then
      error_count=$(check_errors "$log_stderr")
      error_kind=$(classify_log_issue "$log_stderr" || true)
      if [ "$error_count" -gt 5 ]; then
        [ "$status" = "PASS" ] && status="WARN"
        details="${details}${error_count} errors in recent stderr. "
      fi
      case "$error_kind" in
        tcc)
          status="FAIL"
          details="${details}Recent stderr shows macOS TCC/Sandbox denial ('Operation not permitted'). "
          ;;
        dependency)
          [ "$status" = "PASS" ] && status="WARN"
          details="${details}Recent stderr shows missing Python dependency. "
          ;;
        syntax)
          status="FAIL"
          details="${details}Recent stderr shows syntax error. "
          ;;
        schema)
          status="FAIL"
          details="${details}Recent stderr shows DB/schema mismatch. "
          ;;
      esac
    fi
  fi

  [ -z "$details" ] && details="All checks passed"

  # HEALED counts as PASS for overall status
  case "$status" in
    PASS|HEALED) TOTAL_PASS=$((TOTAL_PASS + 1)) ;;
    WARN) TOTAL_WARN=$((TOTAL_WARN + 1)) ;;
    FAIL)
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
      FAILED_MONITORS+=("${name}|${plist_id}|${log_stdout}|${log_stderr}|${proc_grep}|${schedule}|${mon_type}|${details}")
      ;;
  esac

  # JSON
  escaped_details=$(json_escape "$details")
  json_item="    {\"name\":\"$name\",\"plist\":\"$plist_id\",\"status\":\"$status\",\"type\":\"$mon_type\",\"loaded\":\"$loaded\",\"process\":\"$proc_alive\",\"last_activity\":\"$stale_age\",\"stderr_errors\":$error_count,\"schedule\":\"$schedule\",\"details\":\"$escaped_details\"}"
  [ -n "$JSON_AGENTS" ] && JSON_AGENTS="${JSON_AGENTS},
${json_item}" || JSON_AGENTS="$json_item"

  # Report
  case "$status" in
    PASS) icon="PASS" ;; HEALED) icon="HEAL" ;; WARN) icon="WARN" ;; FAIL) icon="FAIL" ;;
  esac
  REPORT_LINES="${REPORT_LINES}  [${icon}] ${name} (${schedule})
         Loaded: ${loaded} | Process: ${proc_alive} | Last: ${stale_age} | Errors: ${error_count}
         ${details}
"
done

# --- Cron job checks ---
CRON_JSON=""
CRON_REPORT=""
for cron_entry in ${CRON_MONITORS[@]+"${CRON_MONITORS[@]}"}; do
  IFS='|' read -r name script check_path max_stale schedule <<< "$cron_entry"

  c_status="PASS"
  c_details=""
  age_str="n/a"

  # Check script exists and is executable
  if [ ! -x "$script" ]; then
    # AUTO-REPAIR: try chmod
    if try_repair_cron "$script"; then
      c_status="HEALED"
      c_details="Self-healed: made executable. "
      TOTAL_HEALED=$((TOTAL_HEALED + 1))
    else
      c_status="FAIL"
      c_details="Script not executable or missing (repair failed). "
    fi
  fi

  # Check output freshness
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
    PASS) icon="PASS" ;; HEALED) icon="HEAL" ;; WARN) icon="WARN" ;; FAIL) icon="FAIL" ;;
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
INTEGRITY=$(sqlite3 "$NEXO_DIR/data/nexo.db" "PRAGMA integrity_check;" 2>/dev/null || echo "CORRUPT")
if [ "$INTEGRITY" != "ok" ]; then
  SQLITE_STATUS="FAIL"
  SQLITE_DETAIL="Integrity check: $INTEGRITY"
  log "CRITICAL: SQLite integrity check failed: $INTEGRITY"
  TOTAL_FAIL=$((TOTAL_FAIL + 1))
  LATEST_BACKUP=$(ls -t "$NEXO_DIR/backups/nexo-"*.db 2>/dev/null | head -1)
  if [ -n "$LATEST_BACKUP" ]; then
    cp "$LATEST_BACKUP" "$NEXO_DIR/data/nexo.db"
    log "RESTORED from $LATEST_BACKUP"
    SQLITE_DETAIL="${SQLITE_DETAIL}. Restored from backup."
  fi
else
  SQLITE_DETAIL="Integrity OK"
  TOTAL_PASS=$((TOTAL_PASS + 1))
fi

# --- Immutable file integrity ---
IMMUTABLE_STATUS="PASS"
IMMUTABLE_DETAIL=""
if [ -f "$HASH_REGISTRY" ]; then
  TAMPERED=0
  while IFS='|' read -r filepath expected_hash; do
    if [ -f "$filepath" ]; then
      ACTUAL=$(shasum -a 256 "$filepath" | cut -d' ' -f1)
      if [ "$ACTUAL" != "$expected_hash" ]; then
        TAMPERED=$((TAMPERED + 1))
        log "CRITICAL: Immutable file modified: $filepath"
        LATEST_SNAP=$(ls -td "$NEXO_HOME/snapshots/"*/ 2>/dev/null | head -1)
        if [ -n "$LATEST_SNAP" ] && [ -f "${LATEST_SNAP}files/${filepath#$HOME_DIR/}" ]; then
          cp "${LATEST_SNAP}files/${filepath#$HOME_DIR/}" "$filepath"
          log "RESTORED immutable file from snapshot"
        fi
      fi
    fi
  done < "$HASH_REGISTRY"
  if [ "$TAMPERED" -gt 0 ]; then
    IMMUTABLE_STATUS="FAIL"
    IMMUTABLE_DETAIL="$TAMPERED immutable files tampered"
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    OBJECTIVE="$CORTEX_DIR/evolution-objective.json"
    if [ -f "$OBJECTIVE" ]; then
      python3 -c "
import json
with open('$OBJECTIVE') as f: d = json.load(f)
d['evolution_enabled'] = False
d['disabled_reason'] = 'Immutable file tampered — watchdog disabled Evolution'
with open('$OBJECTIVE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null
      log "DISABLED Evolution due to immutable file tampering"
    fi
  else
    IMMUTABLE_DETAIL="All files intact"
    TOTAL_PASS=$((TOTAL_PASS + 1))
  fi
else
  IMMUTABLE_DETAIL="No hash registry (skipped)"
  TOTAL_PASS=$((TOTAL_PASS + 1))
fi

# --- Backup freshness ---
BACKUP_STATUS="PASS"
BACKUP_DETAIL=""
LATEST_BACKUP=$(ls -t "$NEXO_DIR/backups/nexo-"*.db 2>/dev/null | head -1)
if [ -n "$LATEST_BACKUP" ]; then
  if $IS_MACOS; then BACKUP_AGE=$(( TS_EPOCH - $(stat -f %m "$LATEST_BACKUP") )); else BACKUP_AGE=$(( TS_EPOCH - $(stat -c %Y "$LATEST_BACKUP") )); fi
  BACKUP_AGE_STR=$(format_age "$BACKUP_AGE")
  if [ "$BACKUP_AGE" -gt 7200 ]; then
    # AUTO-REPAIR: run backup now
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

# --- Cognitive DB check ---
COG_STATUS="PASS"
COG_DETAIL=""
COG_DB="$NEXO_DIR/data/cognitive.db"
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
    "immutable_files": {"status": "$IMMUTABLE_STATUS", "detail": "$(json_escape "$IMMUTABLE_DETAIL")"},
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
  [$IMMUTABLE_STATUS] Immutable Files: $IMMUTABLE_DETAIL
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
# CONSECUTIVE FAILURE TRACKING + NOTIFICATION
# ============================================================================
FAILS=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)
if [ "$TOTAL_FAIL" -gt 0 ]; then
  FAILS=$((FAILS + 1))
  echo "$FAILS" > "$FAIL_COUNT_FILE"
  if [ "$FAILS" -ge "$MAX_FAILS" ]; then
    log "ALERT: $FAILS consecutive runs with failures"
    # Configure your own notification method here (optional)
    # Example: send email, Slack webhook, desktop notification, etc.
    log "NOTIFICATION: $FAILS consecutive failures ($TOTAL_FAIL items FAIL)"
  fi
else
  echo "0" > "$FAIL_COUNT_FILE"
fi

# ============================================================================
# LEVEL 2 AUTO-REPAIR: Launch NEXO for intelligent diagnosis
# ============================================================================
# Only triggers if: (a) there are FAILs after mechanical repair, (b) no NEXO
# repair is already running, (c) no interactive session is active (avoid conflict)
REPAIR_LOCK="$NEXO_HOME/scripts/.watchdog-nexo-repair.lock"
REPAIR_COOLDOWN=1800  # 30 min between NEXO repair attempts

if [ "$TOTAL_FAIL" -gt 0 ]; then
  # Check cooldown — don't spam NEXO invocations
  LOCK_AGE=999999
  SKIP_REPAIR=false
  if [ -f "$REPAIR_LOCK" ]; then
    if $IS_MACOS; then LOCK_AGE=$(( TS_EPOCH - $(stat -f %m "$REPAIR_LOCK" 2>/dev/null || echo 0) )); else LOCK_AGE=$(( TS_EPOCH - $(stat -c %Y "$REPAIR_LOCK" 2>/dev/null || echo 0) )); fi
    if [ "$LOCK_AGE" -lt "$REPAIR_COOLDOWN" ]; then
      log "NEXO repair skipped: cooldown (${LOCK_AGE}s < ${REPAIR_COOLDOWN}s)"
      SKIP_REPAIR=true
    fi
  fi

  if ! $SKIP_REPAIR; then
    # Collect failure details from tracked FAILED_MONITORS array
    FAIL_DETAILS=""
    HAS_CORE_FAILS=false
    for failed in ${FAILED_MONITORS[@]+"${FAILED_MONITORS[@]}"}; do
      IFS='|' read -r m_name m_plist m_stdout m_stderr m_proc m_sched m_type m_details <<< "$failed"
      STDERR_TAIL=""
      if [ -n "$m_stderr" ] && [ -f "$m_stderr" ]; then
        STDERR_TAIL=$(tail -20 "$m_stderr" 2>/dev/null | head -20)
      fi
      STDOUT_TAIL=""
      if [ -n "$m_stdout" ] && [ -f "$m_stdout" ]; then
        STDOUT_TAIL=$(tail -10 "$m_stdout" 2>/dev/null | head -10)
      fi
      [ "$m_type" = "core" ] && HAS_CORE_FAILS=true
      FAIL_DETAILS="${FAIL_DETAILS}
--- ${m_name} (${m_plist}) [${m_type}] ---
Schedule: ${m_sched}
Type: ${m_type}
Failure reason: ${m_details}
Service config: $($IS_MACOS && echo "~/Library/LaunchAgents/${m_plist}.plist" || echo "~/.config/systemd/user/${m_plist}")
Process grep: ${m_proc}
Stderr (last 20 lines):
${STDERR_TAIL}
Stdout (last 10 lines):
${STDOUT_TAIL}
"
    done

    # Only launch if we actually have fail details
    if [ -n "$FAIL_DETAILS" ]; then
      touch "$REPAIR_LOCK"
      log "Launching NEXO Level 2 repair..."

      # Build propagation instructions if core services failed
      # Only runs when NEXO_MAINTAINER=1 and NEXO_PUBLIC_REPO is configured
      PROPAGATE_BLOCK=""
      if [ "${NEXO_MAINTAINER:-}" = "1" ]; then
        NEXO_PUBLIC_REPO="${NEXO_PUBLIC_REPO:-}"
        if $HAS_CORE_FAILS && [ -n "$NEXO_PUBLIC_REPO" ] && [ -d "$NEXO_PUBLIC_REPO/.git" ]; then
          PROPAGATE_BLOCK="
PROPAGATION (for [core] fixes ONLY):
If your fix modifies a file under $NEXO_HOME/ (server.py, db/, plugins/, scripts/):
1. Commit the fix locally with a descriptive message
2. Copy the changed files (sanitized — no personal data) to $NEXO_PUBLIC_REPO/src/
3. Bump patch version in $NEXO_PUBLIC_REPO/package.json
4. Commit + push
5. Create a GitHub release with gh release create
Do NOT propagate fixes for [personal] services — those stay local only."
        fi
      fi

      # Write prompt to temp file (avoids heredoc quoting issues in subshell)
      REPAIR_PROMPT_FILE=$(mktemp /tmp/nexo-repair-XXXXXXXX)
      cat > "$REPAIR_PROMPT_FILE" <<NEXOPROMPT
WATCHDOG LEVEL 2 REPAIR — Automated diagnostic session.

The watchdog detected services that failed mechanical repair (launchctl/systemctl re-registration).
Your job: diagnose the root cause and fix it. Do NOT ask the user anything — resolve autonomously.

Each failure is tagged [core] or [personal]:
- [core] = part of NEXO public package — fix may need propagation to public repo
- [personal] = user-specific service — fix stays local only

FAILURES:
${FAIL_DETAILS}

STEPS:
1. Read the service config (plist on macOS, systemd unit on Linux) to understand the service
2. Check stderr/stdout logs for the actual error
3. Fix the root cause (missing file, bad config, dependency issue, etc.)
4. Reload the service and verify it is running (launchctl on macOS, systemctl on Linux)
5. Log what you did to $NEXO_HOME/logs/watchdog-repair-result.log
${PROPAGATE_BLOCK}

CONSTRAINTS:
- Do NOT modify CLAUDE.md or any protected file
- Do NOT start interactive conversations
- Keep it under 5 minutes
- Log what you did to $NEXO_HOME/logs/watchdog-repair-result.log
NEXOPROMPT

      # Launch NEXO in background with repair task
      # Ensure claude CLI is in PATH (cron/LaunchAgent may have minimal PATH)
      CLAUDE_BIN=$(command -v claude 2>/dev/null || echo "$HOME_DIR/.claude/local/bin/claude")
      if [ ! -x "$CLAUDE_BIN" ]; then
        CLAUDE_BIN=$(find /usr/local/bin /opt/homebrew/bin "$HOME_DIR/.local/bin" "$HOME_DIR/.npm-global/bin" -name claude -type f 2>/dev/null | head -1)
      fi

      if [ -n "$CLAUDE_BIN" ] && [ -x "$CLAUDE_BIN" ]; then
        nohup bash -c "\"$CLAUDE_BIN\" --print --dangerously-skip-permissions -p \"\$(cat '$REPAIR_PROMPT_FILE')\" >> '$LOG_DIR/watchdog-nexo-repair.log' 2>&1; rm -f '$REPAIR_PROMPT_FILE'" &
      else
        log "NEXO repair ABORTED: claude CLI not found in PATH"
        rm -f "$REPAIR_PROMPT_FILE"
      fi

      REPAIR_PID=$!
      log "NEXO repair launched (PID: $REPAIR_PID)"

      # Wait for repair to complete (max 5 min) then verify
      (
        wait_count=0
        while kill -0 $REPAIR_PID 2>/dev/null && [ $wait_count -lt 60 ]; do
          sleep 5
          wait_count=$((wait_count + 1))
        done

        if [ $wait_count -ge 60 ]; then
          log "NEXO repair timed out after 5 min"
          kill $REPAIR_PID 2>/dev/null
        else
          log "NEXO repair completed. Verifying fixes..."
          # Verify each failed monitor
          VERIFY_PASS=0
          VERIFY_FAIL=0
          for failed in ${FAILED_MONITORS[@]+"${FAILED_MONITORS[@]}"}; do
            IFS='|' read -r v_name v_plist v_stdout v_stderr v_proc v_sched v_type v_details <<< "$failed"
            if try_verify_repair "$v_plist" "$v_stdout" "$v_proc" "$v_type"; then
              VERIFY_PASS=$((VERIFY_PASS + 1))
              log "VERIFY OK: $v_name"
            else
              VERIFY_FAIL=$((VERIFY_FAIL + 1))
              log "VERIFY FAIL: $v_name — still broken after repair"
            fi
          done
          log "Post-repair verification: $VERIFY_PASS passed, $VERIFY_FAIL failed"
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] Verification: $VERIFY_PASS OK, $VERIFY_FAIL FAIL" >> "$LOG_DIR/watchdog-nexo-repair.log"
        fi
      ) &
    fi
  fi
fi

# ============================================================================
# LOG SUMMARY
# ============================================================================
log "Complete: PASS=$TOTAL_PASS HEALED=$TOTAL_HEALED WARN=$TOTAL_WARN FAIL=$TOTAL_FAIL"
