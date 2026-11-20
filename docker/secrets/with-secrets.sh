#!/bin/sh
# Entrypoint wrapper for local compose only (Azure sets secrets itself):
#   with-secrets.sh <container> <command...>
# loads /secrets/<container>.env, written by make-secrets.sh, into the
# environment, then replaces itself with the container's real command, which
# keeps PID 1 and receives the stop signal.
set -eu
container=$1
shift
set -a
. "/secrets/$container.env"
set +a
exec "$@"
