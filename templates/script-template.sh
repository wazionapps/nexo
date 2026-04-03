#!/usr/bin/env bash
# nexo: name=example-script
# nexo: description=Example personal shell script using the stable NEXO CLI
# nexo: category=automation
# nexo: runtime=shell
# nexo: timeout=60
# nexo: schedule=08:00
# nexo: schedule_required=false

set -euo pipefail

echo "Hello from NEXO personal shell script"
echo "NEXO_HOME=${NEXO_HOME:-}"
