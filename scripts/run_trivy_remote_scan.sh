#!/usr/bin/env bash

# Retry only fatal registry-throttling failures. A vulnerability result remains
# a single, immediate non-zero exit so the HIGH/CRITICAL gate cannot be diluted.
set -uo pipefail
shopt -s nocasematch

if [[ $# -ne 2 ]]; then
    echo "usage: $0 IMAGE_REF linux/amd64|linux/arm64" >&2
    exit 2
fi

image_ref="$1"
image_platform="$2"
image_pattern='^[A-Za-z0-9][A-Za-z0-9._/@:-]*$'
if [[ ! "$image_ref" =~ $image_pattern ]]; then
    echo "image reference is not a safe literal" >&2
    exit 2
fi
case "$image_platform" in
    linux/amd64 | linux/arm64) ;;
    *)
        echo "unsupported image platform: $image_platform" >&2
        exit 2
        ;;
esac

# Registry credentials are optional. An exported-but-empty TRIVY_USERNAME makes
# Trivy attempt a credentialed handshake with a blank user, so drop the pair
# entirely unless both halves carry a value and fall back to anonymous pulls.
if [[ -z "${TRIVY_USERNAME:-}" || -z "${TRIVY_PASSWORD:-}" ]]; then
    unset TRIVY_USERNAME TRIVY_PASSWORD
fi

max_attempts=3
attempt=1
if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required to classify Trivy's structured vulnerability report" >&2
    exit 2
fi
if ! output_file="$(mktemp)" || [[ -z "$output_file" ]]; then
    echo "could not create secure scan output; refusing to invoke Trivy" >&2
    exit 2
fi
if ! report_file="$(mktemp)" || [[ -z "$report_file" ]]; then
    rm -f "$output_file"
    echo "could not create secure scan report; refusing to invoke Trivy" >&2
    exit 2
fi
trap 'rm -f "$output_file" "$report_file"' EXIT

fatal_pattern='(^|[[:space:]])FATAL([[:space:]:]|$)'
named_throttle_pattern='(^|[^[:alnum:]_])(TOOMANYREQUESTS|pull[[:space:]-]+rate[[:space:]-]+limit)([^[:alnum:]_]|$)'
numeric_throttle_pattern='(^|[^[:alnum:]_])(status[[:space:]]+(code[[:space:]]+)?429|HTTP(/[[:digit:].]+)?[[:space:]]+429|429[[:space:]]+Too[[:space:]]+Many[[:space:]]+Requests)([^[:alnum:]_]|$)'
registry_context_word_pattern='(^|[^[:alnum:]_])(remote[[:space:]]+error|registr(y|ies)|repositor(y|ies)|manifests?|blobs?|artifacts?|image[[:space:]]+scan[[:space:]]+error)([^[:alnum:]_]|$)'
registry_get_pattern='(^|[^[:alnum:]_])GET[[:space:]]+https?://'

has_registry_context_line() {
    local line="$1"

    [[ "$line" =~ $registry_context_word_pattern || "$line" =~ $registry_get_pattern ]]
}

is_fatal_registry_throttle_pair() {
    local first_line="$1"
    local second_line="$2"

    if [[ ! "$first_line" =~ $fatal_pattern && ! "$second_line" =~ $fatal_pattern ]]; then
        return 1
    fi
    if [[ "$first_line" =~ $named_throttle_pattern \
        || "$second_line" =~ $named_throttle_pattern ]]; then
        return 0
    fi
    if [[ ! "$first_line" =~ $numeric_throttle_pattern \
        && ! "$second_line" =~ $numeric_throttle_pattern ]]; then
        return 1
    fi
    has_registry_context_line "$first_line" \
        || has_registry_context_line "$second_line"
}

has_adjacent_fatal_registry_throttle() {
    local scan_output="$1"
    local line
    local previous_line=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        if is_fatal_registry_throttle_pair "$line" ""; then
            return 0
        fi
        if [[ -n "$previous_line" ]] \
            && is_fatal_registry_throttle_pair "$previous_line" "$line"; then
            return 0
        fi
        previous_line="$line"
    done < "$scan_output"
    return 1
}

while (( attempt <= max_attempts )); do
    : > "$output_file"
    : > "$report_file"
    trivy image \
        --image-src remote \
        --platform "$image_platform" \
        --scanners vuln \
        --severity HIGH,CRITICAL \
        --ignorefile .trivyignore.yaml \
        --exit-code 1 \
        --no-progress \
        --timeout 30m \
        --format json \
        --output "$report_file" \
        "$image_ref" > "$output_file" 2>&1
    status=$?
    cat "$output_file"

    if [[ ! -s "$report_file" ]]; then
        if (( status == 0 )); then
            echo "Trivy reported success without a structured report" >&2
            exit 2
        fi
    elif ! jq -e '
        type == "object"
        and (.Results | type == "array")
        and all(.Results[];
            type == "object"
            and (.Target | type == "string")
            and (
                (has("Vulnerabilities") | not)
                or .Vulnerabilities == null
                or (
                    (.Vulnerabilities | type == "array")
                    and all(.Vulnerabilities[];
                        type == "object"
                        and (.VulnerabilityID | type == "string")
                        and (.Severity | type == "string")
                    )
                )
            )
        )
    ' "$report_file" >/dev/null 2>&1; then
        cat "$report_file"
        echo "Trivy emitted an invalid structured report; refusing to retry" >&2
        (( status == 0 )) && status=2
        exit "$status"
    else
        jq -e '
            [.Results[].Vulnerabilities[]?
             | select(.Severity == "HIGH" or .Severity == "CRITICAL")]
            | length > 0
        ' "$report_file" >/dev/null
        classification_status=$?
        if (( classification_status == 0 )); then
            jq -r '
                .Results[] as $result
                | $result.Vulnerabilities[]?
                | select(.Severity == "HIGH" or .Severity == "CRITICAL")
                | [$result.Target, .VulnerabilityID, .Severity, .PkgName,
                   .InstalledVersion, (.FixedVersion // "-")]
                | @tsv
            ' "$report_file"
            (( status == 0 )) && status=1
            exit "$status"
        fi
        if (( classification_status != 1 )); then
            cat "$report_file"
            echo "Trivy report classification failed; refusing to retry" >&2
            (( status == 0 )) && status=2
            exit "$status"
        fi
    fi

    if (( status == 0 )) && grep -Eiq '(^|[[:space:]])FATAL([[:space:]:]|$)' "$output_file"; then
        echo "Trivy returned success with a fatal diagnostic; refusing to accept the scan" >&2
        exit 2
    fi
    if (( status == 0 )); then
        exit 0
    fi

    if ! has_adjacent_fatal_registry_throttle "$output_file"; then
        exit "$status"
    fi
    if (( attempt == max_attempts )); then
        echo "remote scan failed after $max_attempts attempts due to a transient registry throttle" >&2
        exit "$status"
    fi

    delay=$((attempt * 5))
    echo "transient registry throttle on attempt $attempt/$max_attempts; retrying in ${delay}s" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
done

exit 1
