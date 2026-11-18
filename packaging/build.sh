#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d ' \n' < "$ROOT/VERSION")"
export VERSION
mkdir -p "$ROOT/dist"
cd "$ROOT"
nfpm package --config packaging/nfpm.yaml --packager deb --target dist/
echo "built dist/atis-relay-host_${VERSION}_all.deb"
