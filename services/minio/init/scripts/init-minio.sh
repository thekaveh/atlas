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
# optional comma-separated extra bucket vars.
# Format: CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:EXTRA_BUCKET_VAR,...]
for entry in \
    "comfyui:MINIO_BUCKET_COMFYUI:MINIO_COMFYUI_ACCESS_KEY:MINIO_COMFYUI_SECRET_KEY" \
    "backend:MINIO_BUCKET_BACKEND:MINIO_BACKEND_ACCESS_KEY:MINIO_BACKEND_SECRET_KEY" \
    "n8n:MINIO_BUCKET_N8N:MINIO_N8N_ACCESS_KEY:MINIO_N8N_SECRET_KEY" \
    "jupyter:MINIO_BUCKET_JUPYTER:MINIO_JUPYTER_ACCESS_KEY:MINIO_JUPYTER_SECRET_KEY" \
    "docling:MINIO_BUCKET_DOCLING:MINIO_DOCLING_ACCESS_KEY:MINIO_DOCLING_SECRET_KEY" \
    "iceberg:MINIO_BUCKET_ICEBERG_LAKEHOUSE:MINIO_ICEBERG_ACCESS_KEY:MINIO_ICEBERG_SECRET_KEY:MINIO_BUCKET_ICEBERG_JARS,MINIO_BUCKET_ICEBERG_CHECKPOINTS,MINIO_BUCKET_ICEBERG_LANDING" \
; do
    consumer=$(echo "$entry" | cut -d: -f1)
    bucket_var=$(echo "$entry" | cut -d: -f2)
    access_var=$(echo "$entry" | cut -d: -f3)
    secret_var=$(echo "$entry" | cut -d: -f4)
    extra_bucket_vars=$(echo "$entry" | cut -d: -f5-)

    # Resolve variable values via indirection
    eval "bucket=\${$bucket_var:-}"
    eval "access=\${$access_var:-}"
    eval "secret=\${$secret_var:-}"

    if [ -z "$bucket" ] || [ -z "$access" ] || [ -z "$secret" ]; then
        echo "minio-init: ERROR — missing env for consumer '$consumer' ($bucket_var/$access_var/$secret_var)" >&2
        exit 1
    fi

    buckets="$bucket"
    if [ "$extra_bucket_vars" != "$entry" ] && [ -n "$extra_bucket_vars" ]; then
        old_ifs=$IFS
        IFS=,
        for extra_bucket_var in $extra_bucket_vars; do
            IFS=$old_ifs
            eval "extra_bucket=\${$extra_bucket_var:-}"
            if [ -z "$extra_bucket" ]; then
                echo "minio-init: ERROR — missing env for consumer '$consumer' ($extra_bucket_var)" >&2
                exit 1
            fi
            buckets="$buckets $extra_bucket"
            IFS=,
        done
        IFS=$old_ifs
    fi

    # 1. Create bucket(s) (idempotent)
    for provision_bucket in $buckets; do
        echo "minio-init: ensuring bucket 'local/$provision_bucket'..."
        mc mb --ignore-existing "local/$provision_bucket"
    done

    # 2. Write the scoped policy to a tmp file
    policy_file="/tmp/${consumer}-policy.json"
    object_resources=""
    bucket_resources=""
    for policy_bucket in $buckets; do
        if [ -n "$object_resources" ]; then
            object_resources="$object_resources,"
            bucket_resources="$bucket_resources,"
        fi
        object_resources="${object_resources}\"arn:aws:s3:::${policy_bucket}/*\""
        bucket_resources="${bucket_resources}\"arn:aws:s3:::${policy_bucket}\""
    done
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
    }
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
