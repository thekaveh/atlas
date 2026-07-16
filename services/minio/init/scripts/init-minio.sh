#!/bin/sh
# MinIO bucket + service-account provisioning. Idempotent: re-running is a no-op.
set -eu

echo "minio-init: starting MinIO provisioning..."

# Required env vars
: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"

# Wait for MinIO server (depends_on healthcheck should already guarantee this, but be defensive)
echo "minio-init: waiting for MinIO at http://minio:9000..."
i=0
until mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        echo "minio-init: ERROR — could not reach MinIO after 30 attempts; aborting" >&2
        exit 1
    fi
    sleep 2
done
echo "minio-init: alias 'local' configured"

# Each consumer: primary bucket name var, access-key var, secret-key var,
# optional comma-separated writable and read-only bucket vars.
# Format: CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:RW_BUCKET_VAR,...[:RO_BUCKET_VAR,...]]
consumer_entries='
comfyui:MINIO_BUCKET_COMFYUI:MINIO_COMFYUI_ACCESS_KEY:MINIO_COMFYUI_SECRET_KEY
backend:MINIO_BUCKET_BACKEND:MINIO_BACKEND_ACCESS_KEY:MINIO_BACKEND_SECRET_KEY
n8n:MINIO_BUCKET_N8N:MINIO_N8N_ACCESS_KEY:MINIO_N8N_SECRET_KEY
jupyter:MINIO_BUCKET_JUPYTER:MINIO_JUPYTER_ACCESS_KEY:MINIO_JUPYTER_SECRET_KEY
spark:MINIO_BUCKET_SPARK_HISTORY:MINIO_SPARK_ACCESS_KEY:MINIO_SPARK_SECRET_KEY:MINIO_BUCKET_ICEBERG_LAKEHOUSE,MINIO_BUCKET_ICEBERG_JARS,MINIO_BUCKET_ICEBERG_CHECKPOINTS,MINIO_BUCKET_ICEBERG_LANDING
docling:MINIO_BUCKET_DOCLING:MINIO_DOCLING_ACCESS_KEY:MINIO_DOCLING_SECRET_KEY
langfuse:MINIO_BUCKET_LANGFUSE:MINIO_LANGFUSE_ACCESS_KEY:MINIO_LANGFUSE_SECRET_KEY
mlflow:MINIO_BUCKET_MLFLOW:MINIO_MLFLOW_ACCESS_KEY:MINIO_MLFLOW_SECRET_KEY
label-studio:MINIO_BUCKET_LABEL_STUDIO:MINIO_LABEL_STUDIO_ACCESS_KEY:MINIO_LABEL_STUDIO_SECRET_KEY
iceberg:MINIO_BUCKET_ICEBERG_LAKEHOUSE:MINIO_ICEBERG_ACCESS_KEY:MINIO_ICEBERG_SECRET_KEY:MINIO_BUCKET_ICEBERG_JARS,MINIO_BUCKET_ICEBERG_CHECKPOINTS,MINIO_BUCKET_ICEBERG_LANDING
asset-ingest:MINIO_BUCKET_ASSET_INPUTS:MINIO_ASSET_INGEST_ACCESS_KEY:MINIO_ASSET_INGEST_SECRET_KEY
asset-worker:ASSET_WORKER_MINIO_BUCKET:MINIO_ASSET_WORKER_ACCESS_KEY:MINIO_ASSET_WORKER_SECRET_KEY::MINIO_BUCKET_ASSET_INPUTS
asset-baker:ASSET_BAKER_MINIO_BUCKET:MINIO_ASSET_BAKER_ACCESS_KEY:MINIO_ASSET_BAKER_SECRET_KEY::MINIO_BUCKET_ASSET_INPUTS
'

if [ -n "${MINIO_EXTRA_CONSUMERS:-}" ]; then
    for extra_entry in $MINIO_EXTRA_CONSUMERS; do
        consumer_entries="${consumer_entries}
${extra_entry}"
    done
fi

printf '%s\n' "$consumer_entries" | while IFS= read -r entry; do
    [ -z "$entry" ] && continue

    consumer=$(echo "$entry" | cut -d: -f1)
    bucket_var=$(echo "$entry" | cut -d: -f2)
    access_var=$(echo "$entry" | cut -d: -f3)
    secret_var=$(echo "$entry" | cut -d: -f4)
    extra_bucket_vars=$(echo "$entry" | cut -d: -f5)
    read_only_bucket_vars=$(echo "$entry" | cut -d: -f6-)

    if [ -z "$consumer" ] || [ -z "$bucket_var" ] || [ -z "$access_var" ] || [ -z "$secret_var" ]; then
        echo "minio-init: ERROR — invalid consumer entry '$entry'; expected CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:RW_BUCKET_VAR,...[:RO_BUCKET_VAR,...]]" >&2
        exit 1
    fi

    # Resolve variable values via indirection
    eval "bucket=\${$bucket_var:-}"
    eval "access=\${$access_var:-}"
    eval "secret=\${$secret_var:-}"

    if [ -z "$bucket" ] || [ -z "$access" ] || [ -z "$secret" ]; then
        echo "minio-init: ERROR — missing env for consumer '$consumer' ($bucket_var/$access_var/$secret_var)" >&2
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
                echo "minio-init: ERROR — missing env for consumer '$consumer' ($extra_bucket_var)" >&2
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
                echo "minio-init: ERROR — missing env for consumer '$consumer' ($read_only_bucket_var)" >&2
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
        echo "minio-init: ensuring bucket 'local/$provision_bucket'..."
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
      "Action": ["s3:ListBucket"],
      "Resource": ['"$read_only_bucket_resources"']
    }'
    fi
    cat > "$policy_file" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": [${object_resources}]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": [${bucket_resources}]
    }${read_only_statements}
  ]
}
EOF

    # 3. Create or update the named policy (idempotent). NOTE: this named
    # policy is never ATTACHED to anything — the service account below
    # carries its policy inline (step 4). It's maintained purely so
    # operators can inspect the per-consumer grants in the MinIO console.
    policy_name="${consumer}-policy"
    if mc admin policy info local "$policy_name" >/dev/null 2>&1; then
        echo "minio-init: updating existing policy '$policy_name'..."
        # `mc admin policy create` overwrites if the name exists in recent mc versions;
        # older versions require remove+create. Try create; if it errors, do the dance.
        if ! mc admin policy create local "$policy_name" "$policy_file" 2>/dev/null; then
            mc admin policy remove local "$policy_name" || true
            mc admin policy create local "$policy_name" "$policy_file"
        fi
    else
        echo "minio-init: creating policy '$policy_name'..."
        mc admin policy create local "$policy_name" "$policy_file"
    fi

    # 4. Create the service account; on re-runs, refresh its secret +
    # inline policy so .env rotations and bucket renames actually
    # propagate (the old skip-if-exists path silently never updated
    # either).
    if mc admin user svcacct info local "$access" >/dev/null 2>&1; then
        echo "minio-init: service account '$access' exists - refreshing secret + policy..."
        mc admin user svcacct edit local "$access" \
            --secret-key "$secret" \
            --policy "$policy_file"
    else
        echo "minio-init: creating service account '$access' for bucket(s) '$buckets'..."
        mc admin user svcacct add local "$MINIO_ROOT_USER" \
            --access-key "$access" \
            --secret-key "$secret" \
            --policy "$policy_file"
    fi

    rm -f "$policy_file"
done

echo "minio-init: provisioning complete"
