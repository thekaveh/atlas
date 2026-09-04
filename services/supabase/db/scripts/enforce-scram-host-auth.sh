#!/bin/sh
set -eu

# POSTGRES_HOST_AUTH_METHOD is consumed only by initdb.  Existing Atlas
# volumes therefore need a startup guard that contracts legacy host trust/md5
# rules before the durable postmaster accepts connections.
pgdata=${PGDATA:-/var/lib/postgresql/data}
hba="$pgdata/pg_hba.conf"
entrypoint=${ATLAS_POSTGRES_ENTRYPOINT:-/usr/local/bin/docker-entrypoint.sh}

validate_hba() {
  awk '
    function fail(message) {
      print "supabase-db: invalid pg_hba.conf line " NR ": " message > "/dev/stderr"
      invalid = 1
    }
    /^[[:space:]]*($|#)/ { next }
    {
      record = $1
      if (record == "local") {
        if (NF < 4) fail("local record is missing an authentication method")
        next
      }
      if (record ~ /^host/) {
        if (NF < 5) {
          fail("host record is missing an authentication method")
          next
        }
        if ($5 == "trust" || $5 == "password" || $5 == "md5") {
          fail("host authentication did not converge to scram-sha-256")
        }
        next
      }
      fail("unsupported record type " record)
    }
    END { exit invalid ? 1 : 0 }
  ' "$1"
}

if [ -s "$pgdata/PG_VERSION" ] && [ -f "$hba" ]; then
  candidate="$pgdata/.pg_hba.conf.atlas.$$"
  rendered="$candidate.rendered"
  backup="$pgdata/pg_hba.conf.atlas.bak"
  backup_candidate="$backup.$$"
  trap 'rm -f "$candidate" "$rendered" "$backup_candidate"' EXIT HUP INT TERM

  if awk 'BEGIN { found=0 } $1 ~ /^include(_if_exists|_dir)?$/ { found=1 } END { exit found ? 0 : 1 }' "$hba"; then
    echo "supabase-db: refusing pg_hba.conf upgrade: include directives require explicit operator review" >&2
    exit 1
  fi
  if awk 'BEGIN { found=0 } $1 ~ /^host/ && $5 == "md5" { found=1 } END { exit found ? 0 : 1 }' "$hba"; then
    echo "supabase-db: refusing pg_hba.conf upgrade: md5 rules may serve MD5-only role verifiers; migrate verifiers before requiring SCRAM" >&2
    exit 1
  fi

  # Keep an exact, permission-preserving recovery copy before validating or
  # replacing anything. The candidate inherits the original ownership/mode;
  # only its content is rewritten.
  cp -p "$hba" "$backup_candidate"
  mv "$backup_candidate" "$backup"
  cp -p "$hba" "$candidate"
  awk '
    $1 ~ /^host/ && ($5 == "trust" || $5 == "password") {
      $5 = "scram-sha-256"
    }
    { print }
  ' "$hba" > "$rendered"
  cp "$rendered" "$candidate"
  rm -f "$rendered"

  if ! validate_hba "$candidate"; then
    echo "supabase-db: refusing invalid pg_hba.conf replacement; original preserved" >&2
    exit 1
  fi
  if ! cmp -s "$candidate" "$hba"; then
    mv "$candidate" "$hba"
    if ! validate_hba "$hba"; then
      cp -p "$backup" "$hba"
      echo "supabase-db: pg_hba.conf post-write validation failed; backup restored" >&2
      exit 1
    fi
    echo "supabase-db: upgraded host authentication rules to scram-sha-256"
  fi
  trap - EXIT HUP INT TERM
fi

exec "$entrypoint" postgres "$@"
