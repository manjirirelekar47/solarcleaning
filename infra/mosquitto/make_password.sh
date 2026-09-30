#!/usr/bin/env bash
# Create infra/mosquitto/passwd for one MQTT user (run once per user).
# Usage:  ./infra/mosquitto/make_password.sh backend 'choose-a-long-password'
#         ./infra/mosquitto/make_password.sh esp32   'another-long-password' --append
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <username> <password> [--append]" >&2
  exit 2
fi
user="$1"; pass="$2"; mode="${3:-}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
flag="-c"                       # -c creates (and overwrites) the file
[ "$mode" = "--append" ] && flag=""   # without -c the user is added to the existing file

docker run --rm -v "$here":/work eclipse-mosquitto:2 \
  mosquitto_passwd $flag -b /work/passwd "$user" "$pass"
# 0644, not 0600: the broker drops privileges to its own user and must be able to read the file.
# It holds only salted hashes and is git-ignored.
chmod 644 "$here/passwd" || true
echo "Saved user '$user' to $here/passwd (this file is git-ignored)."
