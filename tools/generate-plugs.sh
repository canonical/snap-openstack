#!/bin/bash
set -euo pipefail

# Regenerate tools/snap-openstack.plugs.data from the snap-declaration of the
# published openstack snap. The output is passed to `snap-review --plugs=`
# in CI (see .github/workflows/build-snap.yml).
# Requires snapd with access to the snap store.

cd "$(dirname "$0")/.."

tmp=$(mktemp)
snap known snap-declaration snap-name=openstack |
    sed '/^$/Q' |
    python3 -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(sys.stdin)["plugs"], indent=2))' \
        > "$tmp"
mv "$tmp" tools/snap-openstack.plugs.data