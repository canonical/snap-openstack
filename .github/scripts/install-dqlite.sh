#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  autoconf automake build-essential liblz4-dev libsqlite3-dev libtool libuv1-dev pkg-config

build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT

git clone --depth 1 --branch lts-1.17.x https://github.com/canonical/dqlite "$build_dir"
cd "$build_dir"
autoreconf -i
./configure --enable-build-raft --prefix=/usr/local
make -j"$(nproc)"
sudo make install
sudo ldconfig
