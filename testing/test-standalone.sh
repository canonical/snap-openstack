#!/bin/bash
set -x
export COLUMNS=256

if [$# -ne 1]; then
    echo "Invalid number of arguments" >&2
    echo "Usage:"
    echo "  $0 <openstack_snap.snap>"
fi

TEST_SNAP_OPENSTACK=${1}

if [[ ! -f "${TEST_SNAP_OPENSTACK}" ]]; then
    echo "${TEST_SNAP_OPENSTACK}: No such file or directory" >&2
    exit 1
fi

# Check docker, containerd and remove them if exists
sudo apt remove --purge docker.io containerd runc -y
sudo rm -rf /run/containerd

# Allow lxd controller to reach to k8s controller on loadbalancer ip
# sudo nft insert rule ip filter FORWARD tcp dport 17070 accept
# sudo nft insert rule ip filter FORWARD tcp sport 17070 accept
# With above rules, got the following error:
# api.charmhub.io on 10.152.183.182:53: server misbehaving
# Accept all packets filtered for forward
sudo nft chain ip filter FORWARD '{policy accept;}'

sudo snap remove --purge lxd
sudo snap install --channel 3.6 juju

sudo snap install --dangerous ${TEST_SNAP_OPENSTACK}
sudo snap connect openstack:juju-bin juju:juju-bin
openstack.sunbeam prepare-node-script --bootstrap | bash -x
sudo snap connect openstack:dot-local-share-juju
sudo snap connect openstack:dot-config-openstack
sudo snap connect openstack:dot-local-share-openstack

# Even though `--topology single --database single` is not used in the
# single-node tutorial, explicitly speficy it here to force the single
# mysql mode.
# The tutorial assumes ~16 GiB of memory where Sunbeam selects the singe
# mysql single mysql mode automatically. And self-hosted runners may
# have more than 32 GiB of memory where Sunbeam selects the multi mysql
# mode instead. So we have to override the Sunbeam's decision to be
# closer to the tutorial scenario.
sg snap_daemon "openstack.sunbeam cluster bootstrap --manifest .github/assets/testing/edge.yml --accept-defaults --topology single --database single"
sg snap_daemon "openstack.sunbeam cluster list"
# Note: Moving configure before enabling caas just to ensure caas images are not downloaded
# To download caas image, require ports to open on firewall to access fedora images.
sg snap_daemon "openstack.sunbeam configure --accept-defaults --openrc demo-openrc"
sg snap_daemon "openstack.sunbeam launch --name test"
# The cloud-init process inside the VM takes ~2 minutes to bring up the
# SSH service after the VM gets ACTIVE in OpenStack
sleep 300
source demo-openrc
openstack console log show --lines 200 test
demo_floating_ip="$(openstack floating ip list -c 'Floating IP Address' -f value | head -n1)"
ssh -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null -i ~/snap/openstack/current/sunbeam "ubuntu@${demo_floating_ip}" true

sg snap_daemon "openstack.sunbeam enable orchestration"
sg snap_daemon "openstack.sunbeam enable loadbalancer"
sg snap_daemon "openstack.sunbeam enable dns testing.github."
# Disabled until https://github.com/canonical/mysql-router-k8s-operator/issues/452
# or corresponding juju bug is fixed
# sg snap_daemon "openstack.sunbeam disable dns"
# sg snap_daemon "openstack.sunbeam disable loadbalancer"
# sg snap_daemon "openstack.sunbeam disable orchestration"

# Vault has storage requirements > 15G
# Commenting as CI servers might not have enough disk space
# sg snap_daemon "openstack.sunbeam enable vault --dev-mode"
# sg snap_daemon "openstack.sunbeam enable secrets"
# sg snap_daemon "openstack.sunbeam disable secrets"
# sg snap_daemon "openstack.sunbeam disable vault"

# Disable caas temporarily while MySQL memory gets adjusted
# sg snap_daemon "openstack.sunbeam enable caas"
# sg snap_daemon "openstack.sunbeam enable validation"
# If smoke tests fails, logs should be collected via sunbeam command in "Collect logs"
# sg snap_daemon "openstack.sunbeam validation run smoke"
# sg snap_daemon "openstack.sunbeam validation run --output tempest_validation.log"
# sg snap_daemon "openstack.sunbeam disable caas"
# sg snap_daemon "openstack.sunbeam disable validation"

sg snap_daemon "openstack.sunbeam enable telemetry"
# Commenting observability as storage requirements ~6G
# sg snap_daemon "openstack.sunbeam enable observability embedded"
# Commented disabling observability due to LP#1998282
# sg snap_daemon "openstack.sunbeam disable observability embedded"
# sg snap_daemon "openstack.sunbeam disable telemetry"

# Commenting features as storage is full in CI machines
# sg snap_daemon "openstack.sunbeam enable resource-optimization"
# sg snap_daemon "openstack.sunbeam enable instance-recovery"
# Disable IR as the consul pods are stuck in getting terminated
# sg snap_daemon "openstack.sunbeam disable instance-recovery"
# sg snap_daemon "openstack.sunbeam disable resource-optimization"
