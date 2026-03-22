#!/bin/bash
# Pre-commit hook: prevent private data from being committed to the public repo.
# Installed by create-nexo or manually: cp scripts/pre-commit-check.sh .git/hooks/pre-commit

RED='\033[0;31m'
NC='\033[0m'

# Add patterns specific to your private data here.
# These are checked against staged files to prevent accidental leaks.
# The pre-commit-check.sh script itself is excluded from scanning.
BLOCKED_PATTERNS=(
    # Add your own patterns below, e.g.:
    # "my-private-api-key"
    # "my-private-domain.com"
    # "my-server-ip"
)

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACMR)

if [ -z "$STAGED_FILES" ]; then
    exit 0
fi

FOUND=0
for pattern in "${BLOCKED_PATTERNS[@]}"; do
    MATCHES=$(echo "$STAGED_FILES" | xargs grep -l "$pattern" 2>/dev/null)
    if [ -n "$MATCHES" ]; then
        echo -e "${RED}BLOCKED: Found private data pattern '$pattern' in:${NC}"
        echo "$MATCHES" | sed 's/^/  /'
        FOUND=1
    fi
done

# Also check for .db files, tokens, credentials
DB_FILES=$(echo "$STAGED_FILES" | grep -E '\.(db|db-wal|db-shm|key|pem)$')
if [ -n "$DB_FILES" ]; then
    echo -e "${RED}BLOCKED: Database/key files staged:${NC}"
    echo "$DB_FILES" | sed 's/^/  /'
    FOUND=1
fi

TOKEN_FILES=$(echo "$STAGED_FILES" | grep -E '_token\.|credentials|\.env$')
if [ -n "$TOKEN_FILES" ]; then
    echo -e "${RED}BLOCKED: Token/credential files staged:${NC}"
    echo "$TOKEN_FILES" | sed 's/^/  /'
    FOUND=1
fi

if [ $FOUND -eq 1 ]; then
    echo ""
    echo -e "${RED}Commit blocked. Remove private data before pushing to public repo.${NC}"
    exit 1
fi

exit 0
