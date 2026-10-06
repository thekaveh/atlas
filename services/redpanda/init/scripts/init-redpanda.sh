#!/usr/bin/env bash
set -euo pipefail

BROKERS="redpanda:9092"  # rpk calls below use -X brokers=redpanda:9092
# `-`, not `:-`: an explicitly blank value means "create no demo topics".
TOPICS=()
IFS=',' read -ra TOPICS <<< "${REDPANDA_DEMO_TOPICS-atlas_stream_events}"

for raw_topic in ${TOPICS[@]+"${TOPICS[@]}"}; do
  topic="$(echo "${raw_topic}" | xargs)"
  if [[ -z "${topic}" ]]; then
    continue
  fi

  echo "Creating Redpanda topic: ${topic}"
  rpk topic create "${topic}" --if-not-exists -X brokers="${BROKERS}"
done
