#!/bin/sh
set -eu

# POSTGRES_HOST_AUTH_METHOD is consumed only by initdb, so a data directory
# initialized with trust or password keeps those host rules in its own
# pg_hba.conf. This script rewrites trust/password host rules in that file to
# scram-sha-256 before the postmaster starts. Compose passes `-D /etc/postgresql`,
# so the rewrite changes no rule the server enforces (see the next comment).
pgdata=${PGDATA:-/var/lib/postgresql/data}
hba="$pgdata/pg_hba.conf"
entrypoint=${ATLAS_POSTGRES_ENTRYPOINT:-/usr/local/bin/docker-entrypoint.sh}

# With the image's own config (`-D /etc/postgresql`) the server reads
# /etc/postgresql/pg_hba.conf, so the data-directory file is inert: a rule
# this guard would refuse there is reported, not fatal. The image's file already
# requires scram-sha-256 on every non-loopback range. This guard does not change
# that file, so in the compose setup it enforces nothing.
#
# The checks below read a host line's method from column 5 (`host db user
# CIDR method`) or column 6 (`host db user addr mask method`), chosen by whether
# column 4 is an IP address.
data_hba_in_use=1
case " $* " in
  *" -D /etc/postgresql "*) data_hba_in_use=0 ;;
esac

refuse_hba_upgrade() {
  reason=$1
  shift
  if [ "$data_hba_in_use" = 1 ]; then
    echo "supabase-db: refusing pg_hba.conf upgrade: $reason" >&2
    exit 1
  fi
  echo "supabase-db: leaving the unused data-directory pg_hba.conf as is: $reason" >&2
  rm -f "$candidate" "$rendered" "$backup_candidate"
  trap - EXIT HUP INT TERM
  exec "$entrypoint" postgres "$@"
}

validate_hba() {
  awk '
    # A host line with a netmask column (`addr mask method`) keeps its method
    # in column 6; a CIDR or hostname keeps it in column 5.
    function is_ip(s) {
      return s ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || (s ~ /:/ && s ~ /^[0-9a-fA-F:]+$/)
    }
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
        m = is_ip($4) ? 6 : 5
        if (NF < m) {
          fail("host record is missing an authentication method")
          next
        }
        if ($m == "trust" || $m == "password" || $m == "md5") {
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
  trap 'rm -f "$candidate" "$rendered" "$backup_candidate"' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if awk 'BEGIN { found=0 } $1 ~ /^include(_if_exists|_dir)?$/ { found=1 } END { exit found ? 0 : 1 }' "$hba"; then
    refuse_hba_upgrade "include directives require explicit operator review" "$@"
  fi
  if awk 'function is_ip(s) { return s ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || (s ~ /:/ && s ~ /^[0-9a-fA-F:]+$/) }
    BEGIN { found=0 } $1 ~ /^host/ && $(is_ip($4) ? 6 : 5) == "md5" { found=1 } END { exit found ? 0 : 1 }' "$hba"; then
    refuse_hba_upgrade "md5 rules may serve MD5-only role verifiers; migrate verifiers before requiring SCRAM" "$@"
  fi

  # The candidate inherits the original ownership/mode; only its content is
  # rewritten.
  cp -p "$hba" "$candidate"
  awk '
    function is_ip(s) {
      return s ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || (s ~ /:/ && s ~ /^[0-9a-fA-F:]+$/)
    }
    $1 ~ /^host/ {
      m = is_ip($4) ? 6 : 5
      if ($m == "trust" || $m == "password") {
        $m = "scram-sha-256"
      }
    }
    { print }
  ' "$hba" > "$rendered"
  cp "$rendered" "$candidate"
  rm -f "$rendered"
  # Keep an exact, permission-preserving recovery copy before validating or
  # replacing anything -- but never overwrite an existing one with a file
  # that will not change: taken on every start, the second start replaced
  # the pre-upgrade rules with the already converted file.
  if [ ! -e "$backup" ] || ! cmp -s "$candidate" "$hba"; then
    cp -p "$hba" "$backup_candidate"
    mv "$backup_candidate" "$backup"
  fi

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
  # Clear the temporaries before dropping the trap, not after. On the
  # no-change path `cmp -s` matched, so the `mv` above never consumed
  # $candidate -- and $candidate lives inside PGDATA. Dropping the trap first
  # leaves it there for good, one file per container start.
  rm -f "$candidate" "$rendered" "$backup_candidate"
  trap - EXIT HUP INT TERM
fi

exec "$entrypoint" postgres "$@"
