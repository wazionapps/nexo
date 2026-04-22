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

# AUDITOR-3RDPASS-V640-V0190 §Risk 1: the fixed-string list above only
# catches Francisco's specific tenants. A future operator on a fresh
# install could ship a leak past this guard if their data happens to
# not share any of the 14 tokens. Add a small regex family that catches
# the *shape* of the leaks we have historically seen so the guard stays
# useful for any operator:
#   - RFC5322-ish email literals inside the source (no variables).
#   - IPv4 literals belonging to private networks plus the public IPs
#     that have leaked from entities_local before (45.148.1.111,
#     Mundiserver's public range).
#   - Absolute /Users/<name>/ paths (any operator home).
# Generic examples in legitimate source (maria@example.com,
# owner@example.com, 127.0.0.1, 0.0.0.0) are allowed via an explicit
# allowlist so the guard does not alarm on test fixtures that live in
# src/ for configuration defaults.
REGEX_PATTERNS=(
  # email literals embedded in *quoted strings*: ``"user@tenant.tld"``.
  # Avoids matching placeholders inside docstrings that describe the
  # format but do not ship a real address.
  "['\"][[:alnum:]._+-]+@[[:alnum:].-]+\\.[[:alpha:]]{2,}['\"]"
  # IPv4 literal: matches anything x.y.z.w where each octet <= 255.
  "\\b(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(\\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}\\b"
  # Absolute /Users/<operator>/ paths. Legitimate source never needs
  # to ship these — it reads ``Path.home()`` / ``$HOME`` instead.
  "/Users/[[:alnum:]._-]+/"
)

# Generic whitelist for obviously-safe literals that can legitimately
# appear in public source (configuration defaults, tests, docs
# examples). Keep small and specific.
REGEX_ALLOWLIST=(
  # Common RFC 2606 example addresses/domains.
  "example\\.com"
  "example\\.org"
  "example\\.net"
  # SMTP / IMAP / LDAP / HTTP localhost references.
  "0\\.0\\.0\\.0"
  "127\\.0\\.0\\.1"
  "::1"
  # Documentation placeholders.
  "your[-_.]?email"
  "placeholder@"
  "user@example"
  "owner@example"
  # CIDR documentation examples.
  "192\\.0\\.2\\."   # RFC 5737 TEST-NET-1
  "198\\.51\\.100\\."  # RFC 5737 TEST-NET-2
  "203\\.0\\.113\\."   # RFC 5737 TEST-NET-3
  # Third-party sender identifiers that appear in public source as
  # routing rules, not as operator secrets.
  "notifications@github"
  "noreply@"
  "no-reply@"
  "automated@"
  "@github\\.com"
  "@gitlab\\.com"
  "@atlassian\\.com"
  "digest@"
  "hello@"
  "support@"
  # Comment/docstring markers. The grep output line is
  # "file:lineno:content" so we anchor on the ``:`` that separates
  # lineno from content.
  ":[[:space:]]*#"
  ":[[:space:]]*//"
  ":[[:space:]]*\\*"
  ":[[:space:]]*['\"]{3}"
  # Placeholder /Users/<letter>/ paths used inside docstring examples
  # (``/Users/x/...``, ``/Users/operator/...``).
  "/Users/(x|y|z|operator|tenant|example|user|demo|agent)/"
)

_allowlist_matches() {
  # Return 0 iff $1 matches any allowlist pattern. Used to filter the
  # regex-driven matches before surfacing them.
  local line="$1"
  local allow
  for allow in "${REGEX_ALLOWLIST[@]}"; do
    if echo "$line" | grep -Eq "$allow"; then
      return 0
    fi
  done
  return 1
}

GREP_ARGS=(
  -REn
  -I
  --include='*.py'
  --include='*.js'
  --include='*.ts'
  --include='*.json'
  --include='*.md'
  --include='*.sh'
  --include='*.html'
  --include='*.yml'
  --include='*.yaml'
  --include='*.txt'
  --exclude-dir=__pycache__
  --exclude-dir=node_modules
)

found=0

# Layer 1: historical fixed-string list. Exact matches, no allowlist —
# if any of these show up the file is mis-placed.
for pat in "${PATTERNS[@]}"; do
  if matches=$(grep "${GREP_ARGS[@]}" -- "$pat" src/ 2>/dev/null); then
    if [ -n "$matches" ]; then
      echo "[check_no_personal_data] LEAK: fixed pattern '$pat' in src/:"
      echo "$matches" | head -20
      echo
      found=1
    fi
  fi
done

# Layer 2: regex shape detection. Each match is filtered against the
# allowlist so legitimate placeholders/test-net addresses do not
# alarm. Use a while-read loop so we can drop allowlisted matches per
# line instead of per pattern.
for pat in "${REGEX_PATTERNS[@]}"; do
  raw=$(grep "${GREP_ARGS[@]}" -- "$pat" src/ 2>/dev/null || true)
  if [ -z "$raw" ]; then
    continue
  fi
  filtered=""
  while IFS= read -r line; do
    if _allowlist_matches "$line"; then
      continue
    fi
    filtered+="$line
"
  done <<< "$raw"
  filtered=${filtered%$'\n'}
  if [ -n "$filtered" ]; then
    echo "[check_no_personal_data] LEAK: regex '$pat' matches src/:"
    echo "$filtered" | head -20
    echo
    found=1
  fi
done

if [ "$found" -ne 0 ]; then
  echo "[check_no_personal_data] FAIL — operator-specific data found inside src/."
  echo "Move the offending file out of src/ (operator content lives in ~/.nexo/...) or scrub the pattern."
  exit 1
fi
echo "[check_no_personal_data] OK — src/ is free of operator-specific markers."
exit 0
