#!/usr/bin/env bash

function create_loop_devices() {
    for l in a b c; do
      loop_file="$(sudo mktemp -p /mnt XXXX.img)"
      sudo truncate -s 4G "${loop_file}"
      loop_dev="$(sudo losetup --show -f "${loop_file}")"
      minor="${loop_dev##/dev/loop}"
      # create well-known names
      sudo mknod -m 0660 "/dev/sdi${l}" b 7 "${minor}"
    done
}

run="${1}"
shift

$run "$@"
