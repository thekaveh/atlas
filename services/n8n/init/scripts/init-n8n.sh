#!/bin/sh
set -eu

echo "n8n-init: Starting locked community package installation..."
if [ ! -x /scripts/install-nodes.sh ]; then
  echo "n8n-init: ERROR - install-nodes.sh is missing or not executable."
  exit 1
fi

/scripts/install-nodes.sh
echo "n8n-init: Community package installation completed successfully."
