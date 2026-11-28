#!/bin/sh
# Silo bucket + service-account provisioning. Idempotent: re-running is a no-op.
set -eu

echo "silo-init: starting Silo provisioning..."

# Required env vars
: "${SILO_ROOT_USER:?SILO_ROOT_USER is required}"
: "${SILO_ROOT_PASSWORD:?SILO_ROOT_PASSWORD is required}"

# Wait for Silo server (depends_on healthcheck should already guarantee this, but be defensive)
echo "silo-init: waiting for Silo at http://silo:9000..."
i=0
# Keys on stdin, not argv: argv is world-readable in /proc while this runs.
until printf '%s\n%s\n' "$SILO_ROOT_USER" "$SILO_ROOT_PASSWORD" | mc alias set local http://silo:9000 >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        echo "silo-init: ERROR — could not reach Silo after 30 attempts; aborting" >&2
        exit 1
    fi
    sleep 2
done
echo "silo-init: alias 'local' configured"

# Each consumer: primary bucket name var, access-key var, secret-key var,
# optional comma-separated writable and read-only bucket vars.
# Format: CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:RW_BUCKET_VAR,...[:RO_BUCKET_VAR,...]]
consumer_entries='
comfyui:SILO_BUCKET_COMFYUI:SILO_COMFYUI_ACCESS_KEY:SILO_COMFYUI_SECRET_KEY
backend:SILO_BUCKET_BACKEND:SILO_BACKEND_ACCESS_KEY:SILO_BACKEND_SECRET_KEY
n8n:SILO_BUCKET_N8N:SILO_N8N_ACCESS_KEY:SILO_N8N_SECRET_KEY
jupyter:SILO_BUCKET_JUPYTER:SILO_JUPYTER_ACCESS_KEY:SILO_JUPYTER_SECRET_KEY::SILO_BUCKET_ICEBERG_LAKEHOUSE
spark:SILO_BUCKET_SPARK_HISTORY:SILO_SPARK_ACCESS_KEY:SILO_SPARK_SECRET_KEY:SILO_BUCKET_ICEBERG_LAKEHOUSE,SILO_BUCKET_ICEBERG_JARS,SILO_BUCKET_ICEBERG_CHECKPOINTS,SILO_BUCKET_ICEBERG_LANDING
docling:SILO_BUCKET_DOCLING:SILO_DOCLING_ACCESS_KEY:SILO_DOCLING_SECRET_KEY
langfuse:SILO_BUCKET_LANGFUSE:SILO_LANGFUSE_ACCESS_KEY:SILO_LANGFUSE_SECRET_KEY
mlflow:SILO_BUCKET_MLFLOW:SILO_MLFLOW_ACCESS_KEY:SILO_MLFLOW_SECRET_KEY
label-studio:SILO_BUCKET_LABEL_STUDIO:SILO_LABEL_STUDIO_ACCESS_KEY:SILO_LABEL_STUDIO_SECRET_KEY
iceberg:SILO_BUCKET_ICEBERG_LAKEHOUSE:SILO_ICEBERG_ACCESS_KEY:SILO_ICEBERG_SECRET_KEY:SILO_BUCKET_ICEBERG_JARS,SILO_BUCKET_ICEBERG_CHECKPOINTS,SILO_BUCKET_ICEBERG_LANDING
jenkins:SILO_BUCKET_ICEBERG_JARS:SILO_JENKINS_ACCESS_KEY:SILO_JENKINS_SECRET_KEY
asset-ingest:SILO_BUCKET_ASSET_INPUTS:SILO_ASSET_INGEST_ACCESS_KEY:SILO_ASSET_INGEST_SECRET_KEY
asset-worker:ASSET_WORKER_SILO_BUCKET:SILO_ASSET_WORKER_ACCESS_KEY:SILO_ASSET_WORKER_SECRET_KEY::SILO_BUCKET_ASSET_INPUTS
asset-baker:ASSET_BAKER_SILO_BUCKET:SILO_ASSET_BAKER_ACCESS_KEY:SILO_ASSET_BAKER_SECRET_KEY::SILO_BUCKET_ASSET_INPUTS
'

if [ -n "${SILO_EXTRA_CONSUMERS:-}" ]; then
    for extra_entry in $SILO_EXTRA_CONSUMERS; do
        consumer_entries="${consumer_entries}
${extra_entry}"
    done
fi

# Access keys this run manages. The loop below runs in a pipeline subshell,
# so it records them in a file; the prune step reads that file (#1385).
access_keys_file=/tmp/silo-managed-access-keys
: > "$access_keys_file"

printf '%s\n' "$consumer_entries" | while IFS= read -r entry; do
    [ -z "$entry" ] && continue

    consumer=$(echo "$entry" | cut -d: -f1)
    bucket_var=$(echo "$entry" | cut -d: -f2)
    access_var=$(echo "$entry" | cut -d: -f3)
    secret_var=$(echo "$entry" | cut -d: -f4)
    extra_bucket_vars=$(echo "$entry" | cut -d: -f5)
    read_only_bucket_vars=$(echo "$entry" | cut -d: -f6-)

    if [ -z "$consumer" ] || [ -z "$bucket_var" ] || [ -z "$access_var" ] || [ -z "$secret_var" ]; then
        echo "silo-init: ERROR — invalid consumer entry '$entry'; expected CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:RW_BUCKET_VAR,...[:RO_BUCKET_VAR,...]]" >&2
        exit 1
    fi

    # Resolve variable values via indirection
    eval "bucket=\${$bucket_var:-}"
    eval "access=\${$access_var:-}"
    eval "secret=\${$secret_var:-}"
    echo "$access" >> "$access_keys_file"

    if [ -z "$bucket" ] || [ -z "$access" ] || [ -z "$secret" ]; then
        echo "silo-init: ERROR — missing env for consumer '$consumer' ($bucket_var/$access_var/$secret_var)" >&2
        exit 1
    fi

    writable_buckets="$bucket"
    if [ -n "$extra_bucket_vars" ]; then
        old_ifs=$IFS
        IFS=,
        for extra_bucket_var in $extra_bucket_vars; do
            IFS=$old_ifs
            eval "extra_bucket=\${$extra_bucket_var:-}"
            if [ -z "$extra_bucket" ]; then
                echo "silo-init: ERROR — missing env for consumer '$consumer' ($extra_bucket_var)" >&2
                exit 1
            fi
            writable_buckets="$writable_buckets $extra_bucket"
            IFS=,
        done
        IFS=$old_ifs
    fi

    read_only_buckets=""
    if [ -n "$read_only_bucket_vars" ]; then
        old_ifs=$IFS
        IFS=,
        for read_only_bucket_var in $read_only_bucket_vars; do
            IFS=$old_ifs
            eval "read_only_bucket=\${$read_only_bucket_var:-}"
            if [ -z "$read_only_bucket" ]; then
                echo "silo-init: ERROR — missing env for consumer '$consumer' ($read_only_bucket_var)" >&2
                exit 1
            fi
            read_only_buckets="$read_only_buckets $read_only_bucket"
            IFS=,
        done
        IFS=$old_ifs
    fi

    buckets="$writable_buckets$read_only_buckets"

    # 1. Create bucket(s) (idempotent)
    for provision_bucket in $buckets; do
        echo "silo-init: ensuring bucket 'local/$provision_bucket'..."
        mc mb --ignore-existing "local/$provision_bucket"
    done

    # 2. Write the scoped policy to a tmp file
    policy_file="/tmp/${consumer}-policy.json"
    object_resources=""
    bucket_resources=""
    for policy_bucket in $writable_buckets; do
        if [ -n "$object_resources" ]; then
            object_resources="$object_resources,"
            bucket_resources="$bucket_resources,"
        fi
        object_resources="${object_resources}\"arn:aws:s3:::${policy_bucket}/*\""
        bucket_resources="${bucket_resources}\"arn:aws:s3:::${policy_bucket}\""
    done
    read_only_object_resources=""
    read_only_bucket_resources=""
    for policy_bucket in $read_only_buckets; do
        if [ -n "$read_only_object_resources" ]; then
            read_only_object_resources="$read_only_object_resources,"
            read_only_bucket_resources="$read_only_bucket_resources,"
        fi
        read_only_object_resources="${read_only_object_resources}\"arn:aws:s3:::${policy_bucket}/*\""
        read_only_bucket_resources="${read_only_bucket_resources}\"arn:aws:s3:::${policy_bucket}\""
    done
    read_only_statements=""
    if [ -n "$read_only_object_resources" ]; then
        read_only_statements=',
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": ['"$read_only_object_resources"']
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ['"$read_only_bucket_resources"']
    }'
    fi
    cat > "$policy_file" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                 "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": [${object_resources}]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:GetBucketLocation"],
      "Resource": [${bucket_resources}]
    }${read_only_statements}
  ]
}
EOF

    # 3. Create or update the named policy (idempotent). NOTE: this named
    # policy is never ATTACHED to anything — the service account below
    # carries its policy inline (step 4). It's maintained purely so
    # operators can inspect the per-consumer grants in the Silo console.
    policy_name="${consumer}-policy"
    if mc admin policy info local "$policy_name" >/dev/null 2>&1; then
        echo "silo-init: updating existing policy '$policy_name'..."
        # `mc admin policy create` overwrites if the name exists in recent mc versions;
        # older versions require remove+create. Try create; if it errors, do the dance.
        if ! mc admin policy create local "$policy_name" "$policy_file" 2>/dev/null; then
            mc admin policy remove local "$policy_name" || true
            mc admin policy create local "$policy_name" "$policy_file"
        fi
    else
        echo "silo-init: creating policy '$policy_name'..."
        mc admin policy create local "$policy_name" "$policy_file"
    fi

    # 4. Create the service account; on re-runs, refresh its secret +
    # inline policy so .env rotations and bucket renames actually
    # propagate (the old skip-if-exists path silently never updated
    # either).
    atlas_managed_description="atlas-managed:${consumer}"
    if mc admin user svcacct info local "$access" >/dev/null 2>&1; then
        echo "silo-init: service account '$access' exists - refreshing secret + policy..."
        # mc has no stdin, env or file form for --secret-key (pgsty/mc RELEASE.2026-09-16).
        # This argv exposure is the accepted exception in services/silo/README.md (#1381).
        # stdout carries the keys back, so it goes nowhere; stderr stays so a
        # failure still names its cause (#1974).
        mc admin user svcacct edit local "$access" \
            --secret-key "$secret" \
            --policy "$policy_file" \
            --description "$atlas_managed_description" >/dev/null
    else
        echo "silo-init: creating service account '$access' for bucket(s) '$buckets'..."
        # `svcacct add` prints "Access Key:" and "Secret Key:" on success (#1974).
        # Same --secret-key argv exception as the edit branch above (#1381).
        mc admin user svcacct add local "$SILO_ROOT_USER" \
            --access-key "$access" \
            --secret-key "$secret" \
            --policy "$policy_file" \
            --description "$atlas_managed_description" >/dev/null
    fi

    rm -f "$policy_file"
done

# Revoke superseded accounts (#1385). Only accounts tagged atlas-managed:<consumer>
# are candidates, so an account an operator made by hand survives. An account is
# superseded when its access key is no longer one this run manages (a rotated
# .env key, or a consumer removed from SILO_EXTRA_CONSUMERS).
# The mc image has no sed or grep, so this uses shell parameter expansion only.
json_field() { # json_field <name> <line>: print a JSON string value from one line
    rest=${2#*\""$1"\":\"}
    [ "$rest" = "$2" ] && return 0
    printf '%s\n' "${rest%%\"*}"
}
is_managed_access_key() { # is_managed_access_key <access-key>
    while IFS= read -r managed; do
        [ "$managed" = "$1" ] && return 0
    done < "$access_keys_file"
    return 1
}
prune_superseded_accounts() {
    mc admin user svcacct ls local "$SILO_ROOT_USER" --json 2>/dev/null \
        | while IFS= read -r line; do
            account=$(json_field accessKey "$line")
            [ -n "$account" ] || continue
            description=$(json_field description "$(mc admin user svcacct info local "$account" --json 2>/dev/null)")
            case "$description" in
                atlas-managed:*) ;;
                *) continue ;;
            esac
            if is_managed_access_key "$account"; then
                continue
            fi
            echo "silo-init: revoking superseded service account '$account' (${description#atlas-managed:})"
            mc admin user svcacct rm local "$account" >/dev/null
        done
}
prune_superseded_accounts
rm -f "$access_keys_file"

echo "silo-init: provisioning complete"
