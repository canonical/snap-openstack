# -*- mode: yaml -*-
job_queue: openstack
provision_data:
  distro: noble
global_timeout: 14400  # 4 hours
output_timeout: 5400  # 90 min
test_data:
  attachments:
    - local: repository.tar.gz
  test_cmds: |
    set -ex
    scp ./attachments/test/repository.tar.gz "ubuntu@${DEVICE_IP}:"
    if ssh "ubuntu@${DEVICE_IP}" '
        set -ex
        # LP: #2093303
        sudo mv -v /etc/apt/sources.list{,.bak}
        sudo apt-get update
        # include ~/.local/bin in PATH
        source  ~/.profile
        set -o pipefail
        # LP: #2097451
        # LP: #2102175
        tar xzvf repository.tar.gz
        cd repository/testing/
        ./install_deps.sh
        ./deploy.sh
        cd ../
        ./testing/test-multinode-maas.sh ${OPENSTACK_SNAP_PATH}
    '; then
        scp -r "ubuntu@${DEVICE_IP}:repository/artifacts/" artifacts/ || true
        find artifacts/
    else
        scp -r "ubuntu@${DEVICE_IP}:repository/artifacts/" artifacts/ || true
        find artifacts/
        exit 1
    fi
