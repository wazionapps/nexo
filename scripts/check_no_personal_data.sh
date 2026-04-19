#!/usr/bin/env bash
# Plan Consolidado v6.4.0 — fail the build if operator-specific data
# (personal email addresses, tenant domains, real names) shows up
# inside `src/`. Public source must stay generic.
#
# Companion to the v6.3.1 .gitignore block on entities_local.json.
# Both guard against the same class of bug: a NEXO instance that
# already knows its operator's secrets shouldn't accidentally ship
# them as part of the public repo.
#
# Exit codes:
#   0 — clean
#   1 — leak found (prints offending matches)
#   2 — internal failure (e.g. ripgrep / grep missing)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Operator-specific markers — extend with care. Each one must be a
# substring that has NO legitimate reason to appear in generic
# product source (NEXO Brain).
PATTERNS=(
  "franciscocp@gmail\\.com"
  "franciscoc@systeam\\.es"
  "f\\.cerdapuigserver"
  "info@systeam\\.es"
  "info@wazion\\.com"
  "info@recambiosyaccesoriosbmw\\.com"
  "canarirural\\.com"
  "psicologallucmajor\\.com"
  "compraventabmw\\.com"
  "bmwcreator\\.com"
  "vicshop"
  "mundiserver\\.com"
  "Cerdà"
  "Cerda Puigserver"
)

found=0
for pat in "${PATTERNS[@]}"; do
  # -E: extended regex, -R: recursive, -n: line numbers, -I: skip binary,
  # --include exclude __pycache__ + the script itself.
  if matches=$(grep -REn -I --include='*.py' --include='*.js' --include='*.ts' --include='*.json' --include='*.md' --include='*.sh' --include='*.html' --include='*.yml' --include='*.yaml' --include='*.txt' --exclude-dir=__pycache__ --exclude-dir=node_modules -- "$pat" src/ 2>/dev/null); then
    if [ -n "$matches" ]; then
      echo "[check_no_personal_data] LEAK: pattern '$pat' found in src/:"
      echo "$matches" | head -20
      echo
      found=1
    fi
  fi
done

if [ "$found" -ne 0 ]; then
  echo "[check_no_personal_data] FAIL — operator-specific data found inside src/."
  echo "Move the offending file out of src/ (operator content lives in ~/.nexo/...) or scrub the pattern."
  exit 1
fi
echo "[check_no_personal_data] OK — src/ is free of operator-specific markers."
exit 0
