#!/bin/bash
set -x
export COLUMNS=256

if [$# -ne 1]; then
    echo "Invalid number of arguments" >&2
    echo "Usage:"
    echo "  $0 <openstack_snap.snap>"
fi

TEST_SNAP_OPENSTACK=${1}
TEST_JUJU_CHANNEL=${TEST_JUJU_CHANNEL:-3.6}

if [[ ! -f "${TEST_SNAP_OPENSTACK}" ]]; then
    echo "${TEST_SNAP_OPENSTACK}: No such file or directory" >&2
    exit 1
fi

if [[ -z "$TEST_MAAS_API_KEY" ]];then
    echo "Error: Please define the TEST_MAAS_API_KEY environment variable" >&1
    exit 1
fi

if [[ -z "$TEST_MAAS_URL" ]];then
    echo "Error: Please define the TEST_MAAS_URL environment variable" >&1
    exit 1
fi

if [[ ! -f "$HOME/.ssh/passwordless" ]]; then
    ssh-keygen -b 2048 -t rsa -f $HOME/.ssh/passwordless -q -N ""
fi

function run_snap_daemon {
    sg snap_daemon "$@"
}

sudo snap install --channel ${TEST_JUJU_CHANNEL} juju
sudo snap install  --dangerous  ${TEST_SNAP_OPENSTACK}
sudo snap connect openstack:juju-bin juju:juju-bin
openstack.sunbeam prepare-node-script --bootstrap | bash -x
sudo snap connect openstack:dot-local-share-juju
sudo snap connect openstack:dot-config-openstack
sudo snap connect openstack:dot-local-share-openstack
sudo snap alias openstack.sunbeam sunbeam

run_snap_daemon sunbeam deployment add maas \
  --name my_maas \
  --url ${TEST_MAAS_URL} \
  --token ${TEST_MAAS_API_TOKEN} \


run_snap_daemon sunbeam deployment space map myspace data
run_snap_daemon sunbeam deployment space map myspace internal
run_snap_daemon sunbeam deployment space map myspace management
run_snap_daemon sunbeam deployment space map myspace public
run_snap_daemon sunbeam deployment space map myspace storage
run_snap_daemon sunbeam deployment space map myspace storage-cluster

run_snap_daemon sunbeam deployment validate

run_snap_daemon sunbeam cluster bootstrap

run_snap_daemon sunbeam cluster list

run_snap_daemon sunbeam configure --accept-defaults --openrc demo-openrc
run_snap_daemon sunbeam launch --name test
# The cloud-init process inside the VM takes ~2 minutes to bring up the
# SSH service after the VM gets ACTIVE in OpenStack
sleep 300
source demo-openrc
openstack console log show --lines 200 test
demo_floating_ip="$(openstack floating ip list -c 'Floating IP Address' -f value | head -n1)"
ssh -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null -i ~/snap/openstack/current/sunbeam "ubuntu@${demo_floating_ip}" true

run_snap_daemon sunbeam enable orchestration
run_snap_daemon sunbeam enable loadbalancer
run_snap_daemon sunbeam enable dns testing.github.

run_snap_daemon sunbeam enable vault --dev-mode
run_snap_daemon sunbeam enable secrets
run_snap_daemon sunbeam disable secrets
run_snap_daemon sunbeam disable vault

sg snap_daemon "openstack.sunbeam enable validation"
# sg snap_daemon "openstack.sunbeam validation run smoke"
# sg snap_daemon "openstack.sunbeam validation run --output tempest_validation.log"

sg snap_daemon "openstack.sunbeam enable telemetry"
sg snap_daemon "openstack.sunbeam enable observability embedded"
sg snap_daemon "openstack.sunbeam disable observability embedded"
sg snap_daemon "openstack.sunbeam disable telemetry"

# Commenting features as storage is full in CI machines
# sg snap_daemon "openstack.sunbeam enable resource-optimization"
# sg snap_daemon "openstack.sunbeam enable instance-recovery"
# Disable IR as the consul pods are stuck in getting terminated
# sg snap_daemon "openstack.sunbeam disable instance-recovery"
# sg snap_daemon "openstack.sunbeam disable resource-optimization"
