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
        ssh-import-id lp:freyes
        timeout_loop () {
            local TIMEOUT=90
            while [ "$TIMEOUT" -gt 0 ]; do
              if "$@" > /dev/null 2>&1; then
                  echo "OK"
                  return 0
              fi
              TIMEOUT=$((TIMEOUT - 1))
              sleep 1
            done
            echo "ERROR: $* FAILED"
            ret=1
            return 1
        }
        # http://pad.lv/2093303
        sudo mv -v /etc/apt/sources.list{,.bak}
        # Workaround for:
        #   E: Failed to fetch http://...  Hash Sum mismatch
        timeout_loop sudo apt-get update -q

        # include ~/.local/bin in PATH
        source  ~/.profile
        set -o pipefail
        # LP: #2097451
        # LP: #2102175
        tar xzvf repository.tar.gz
        cd repository/testing/

        # generate passwordless key if needed
        test -f ~/.ssh/passwordless || ssh-keygen -b 2048 -t rsa -f ~/.ssh/passwordless -q -N ""

        # Allow ssh connections to the virtual nodes without having host fingerprint issues.
        echo "Host 172.16.1.* 172.16.2.*" >> ~/.ssh/config
        echo "    UserKnownHostsFile /dev/null" >> ~/.ssh/config
        echo "    StrictHostKeyChecking no" >> ~/.ssh/config

        # Install depependencies in the hypervisor.
        ./install_deps.sh

        # Prepare the testing bed running terragrunt
        # make the libvirt group effective in this shell, so terraform can talk to the libvirt unix socket
        sudo su - ubuntu -c $(realpath ./deploy.sh)
        cd ../

        # Start the testing using the previously prepare test bed.
        export TEST_SNAP_OPENSTACK=${OPENSTACK_SNAP_PATH}
        export TEST_MAAS_API_KEY="$(cat /tmp/maas-api.key)"
        export TEST_MAAS_URL="http://172.16.1.2:5240/MAAS"
        ./testing/test-multinode-maas.sh ${OPENSTACK_SNAP_PATH}
    '; then
        scp -r "ubuntu@${DEVICE_IP}:repository/artifacts/" artifacts/ || true
        find artifacts/
    else
        ssh ubuntu@${DEVICE_IP} /home/ubuntu/repository/testing/collect-logs.sh
        scp -r "ubuntu@${DEVICE_IP}:repository/artifacts/" artifacts/ || true
        find artifacts/
        echo "blocking until file /tmp/.continue shows up in ${DEVICE_IP}"
        echo ssh ubuntu@${DEVICE_IP}
        ssh ubuntu@${DEVICE_IP} "until test -f /tmp/.continue; do sleep 10;done"
        exit 1
    fi
