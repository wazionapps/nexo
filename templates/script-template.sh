#!/usr/bin/env bash
# nexo: name=example-script
# nexo: description=Example personal shell script using the stable NEXO CLI
# nexo: runtime=shell
# nexo: timeout=60

set -euo pipefail

echo "Hello from NEXO personal shell script"
echo "NEXO_HOME=${NEXO_HOME:-}"
