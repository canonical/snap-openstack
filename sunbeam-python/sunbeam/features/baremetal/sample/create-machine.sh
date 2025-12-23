#!/bin/bash

set -xe

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 IPMI_ADDRESS IRONIC_NETWORK"
    exit 1
fi

IPMI_USER="admin"
IPMI_PASS="admin"
SWITCH_INFO="virtual"
SWITCH_ID="00:00:00:00:00:00"

# provide values for the following variables:
IPMI_ADDR="$1"
IRONIC_NETWORK="$2"

# Assume OpenStack credentials are in ~/.openrc, and that the user also has
# system-scoped credentials.
. ~/.openrc

install_dependencies() {
    apt install -y pkg-config qemu-kvm libvirt-daemon-system libvirt-dev ipmitool virtualbmc
    kvm-ok
}

create_machine() {
    machine="$1"
    virsh destroy $machine || true
    virsh undefine $machine || true
    qemu-img create -f qcow2 /var/lib/libvirt/images/$machine.qcow2 40G
    virsh define libvirt/$machine.xml

    # test that it can start.
    virsh start $machine
    virsh destroy $machine
}

create_bmc_machine() {
    machine="$1"
    ipmi_port="$2"
    vbmc add $machine --port $ipmi_port --username $IPMI_USER --password $IPMI_PASS
    vbmc show $machine
    vbmc start $machine
    ipmitool -I lanplus -U $IPMI_USER -P $IPMI_PASS -H 127.0.0.1 -p $ipmi_port power status
}

create_glance_images() {
    wget https://tarballs.openstack.org/ironic-python-agent/tinyipa/files/tinyipa-master.vmlinuz
    wget https://tarballs.openstack.org/ironic-python-agent/tinyipa/files/tinyipa-master.gz

    openstack image create tinyipa-deploy-ipmi.vmlinuz --public --disk-format=raw --container-format=bare --file ./tinyipa-master.vmlinuz
    openstack image create tinyipa-deploy-ipmi.initramfs --public --disk-format=raw --container-format=bare --file ./tinyipa-master.gz

    openstack image import tinyipa-deploy-ipmi.vmlinuz --method copy-image --store swift
    openstack image import tinyipa-deploy-ipmi.initramfs --method copy-image --store swift
}

create_nova_flavor() {
    openstack flavor create --ram 4096 --vcpus 2 --disk 40 metallic-flavor
    openstack flavor set --property resources:VCPU=0 metallic-flavor
    openstack flavor set --property resources:MEMORY_MB=0 metallic-flavor
    openstack flavor set --property resources:DISK_GB=0 metallic-flavor

    # note that CUSTOM_BAREMETAL directly relates to the --resource-class baremetal above.
    openstack flavor set --property resources:CUSTOM_BAREMETAL=1 metallic-flavor
}

create_ironic_machine() {
    machine="$1"
    ipmi_port="$2"
    chassis_id="$(openstack baremetal chassis create -f value -c uuid)"
    openstack baremetal node create --name $machine --driver ipmi --chassis $chassis_id
    openstack baremetal node set $machine \
     --resource-class baremetal \
     --driver-info ipmi_address=$IPMI_ADDR --driver-info ipmi_port=$ipmi_port \
     --driver-info ipmi_username=$IPMI_USER --driver-info ipmi_password=$IPMI_PASS \
     --driver-info deploy_kernel=$DEPLOY_VMLINUZ_UUID \
     --driver-info deploy_ramdisk=$DEPLOY_INITRD_UUID \
     --driver-info cleaning_network=$IRONIC_NETWORK \
     --driver-info provisioning_network=$IRONIC_NETWORK
}

add_ironic_machine_port() {
    machine="$1"
    mac_addr="$2"
    # register a port for the node.
    machine_uuid="$(openstack baremetal node show $machine -f value -c uuid)"
    port_uuid="$(openstack baremetal port create $mac_addr --node $machine_uuid -c uuid -f value)"
    openstack baremetal port set $port_uuid --local-link-connection switch_info=$SWITCH_INFO \
      --local-link-connection switch_id=$SWITCH_ID --local-link-connection port_id=$mac_addr
}

provide_ironic_machine() {
    machine="$1"
    openstack baremetal node validate $machine
    echo "Managing '$machine' and waiting for it to become 'manageable'..."
    openstack baremetal node manage $machine --wait 300
    openstack baremetal node show $machine

    echo "Providing '$machine' and waiting for it to become 'available'... May take a while if automated_cleaning=true..."
    openstack baremetal node provide $machine --wait 600

    openstack baremetal node show $machine
}

main() {
    which virsh || install_dependencies

    pkill -f vbmcd || true
    rm -rf ~/.vbmc/
    vbmcd

    create_machine machine1
    create_bmc_machine machine1 623

    openstack image show tinyipa-deploy-ipmi.vmlinuz > /dev/null || create_glance_images
    DEPLOY_VMLINUZ_UUID="$(openstack image show tinyipa-deploy-ipmi.vmlinuz -f value -c id)"
    DEPLOY_INITRD_UUID="$(openstack image show tinyipa-deploy-ipmi.initramfs -f value -c id)"

    openstack flavor show metallic-flavor > /dev/null || create_nova_flavor

    unset OS_PROJECT_NAME OS_PROJECT_DOMAIN_NAME
    export OS_SYSTEM_SCOPE=all

    openstack baremetal node show machine1 || create_ironic_machine machine1 623

    node_mac_addr="$(virsh domiflist machine1 | awk 'NR == 3' | awk '{print $5}')"
    openstack baremetal port show --address $node_mac_addr || add_ironic_machine_port machine1 $node_mac_addr

    provide_ironic_machine machine1
}

set -euxo pipefail
main
echo "ALL DONE!"
