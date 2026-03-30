#!/bin/bash
# NEXO Daily Briefing — SessionStart hook
# Checks if a briefing should be sent and creates a flag for NEXO to process.
# Does NOT send the email directly (needs Claude to research news).
# Only marks that NEXO should launch the briefing at startup.
# Frequency: Monday, Wednesday, Friday (3x/week)

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BRIEFING_FILE="$NEXO_HOME/operations/.briefing-last-sent"
FLAG_FILE="$NEXO_HOME/operations/.briefing-pending"
TODAY=$(date +%Y-%m-%d)
HOUR=$(date +%H)
DOW=$(date +%u)  # 1=Monday, 7=Sunday

# Only after 8:00 AM — before that counts as "previous day"
if [ "$HOUR" -lt 8 ]; then
    exit 0
fi

# Only Monday (1), Wednesday (3), Friday (5)
if [ "$DOW" != "1" ] && [ "$DOW" != "3" ] && [ "$DOW" != "5" ]; then
    exit 0
fi

# If already sent today, skip
LAST_SENT=$(cat "$BRIEFING_FILE" 2>/dev/null)
if [ "$LAST_SENT" = "$TODAY" ]; then
    exit 0
fi

# Mark briefing as pending for NEXO to launch in background
echo "$TODAY" > "$FLAG_FILE"
echo "briefing-pending"
