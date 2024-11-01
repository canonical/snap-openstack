# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


SUPPORTED_RELEASE = "jammy"
JUJU_CHANNEL = "3.5/stable"
JUJU_BASE = "ubuntu@22.04"
OPENSTACK_CHANNEL = "2024.1/stable"
OVN_CHANNEL = "24.03/stable"
RABBITMQ_CHANNEL = "3.12/stable"
TRAEFIK_CHANNEL = "latest/beta"
MICROCEPH_CHANNEL = "reef/candidate"
SUNBEAM_MACHINE_CHANNEL = "2024.1/stable"
SUNBEAM_CLUSTERD_CHANNEL = "2024.1/stable"
SNAP_SUNBEAM_CLUSTERD_CHANNEL = "2024.1/stable"
MICROK8S_CHANNEL = "legacy/stable"
MYSQL_CHANNEL = "8.0/stable"
CERT_AUTH_CHANNEL = "latest/beta"
BIND_CHANNEL = "9/stable"
VAULT_CHANNEL = "latest/edge"  # Pinned to a specific revision in TF plans
CONSUL_CHANNEL = "1.19/edge"
TEMPEST_CHANNEL = "2024.1/stable"
K8S_CHANNEL = "1.31/candidate"

# List of charms with default channels
OPENSTACK_CHARMS_K8S = {
    "cinder-ceph-k8s": OPENSTACK_CHANNEL,
    "cinder-k8s": OPENSTACK_CHANNEL,
    "glance-k8s": OPENSTACK_CHANNEL,
    "horizon-k8s": OPENSTACK_CHANNEL,
    "keystone-k8s": OPENSTACK_CHANNEL,
    "neutron-k8s": OPENSTACK_CHANNEL,
    "nova-k8s": OPENSTACK_CHANNEL,
    "placement-k8s": OPENSTACK_CHANNEL,
}
OVN_CHARMS_K8S = {
    "ovn-central-k8s": OVN_CHANNEL,
    "ovn-relay-k8s": OVN_CHANNEL,
}
MYSQL_CHARMS_K8S = {
    "mysql-k8s": MYSQL_CHANNEL,
    "mysql-router-k8s": MYSQL_CHANNEL,
}
MISC_CHARMS_K8S = {
    "self-signed-certificates": CERT_AUTH_CHANNEL,
    "rabbitmq-k8s": RABBITMQ_CHANNEL,
    "traefik-k8s": TRAEFIK_CHANNEL,
}
MACHINE_CHARMS = {
    "microceph": MICROCEPH_CHANNEL,
    "microk8s": MICROK8S_CHANNEL,
    "k8s": K8S_CHANNEL,
    "openstack-hypervisor": OPENSTACK_CHANNEL,
    "sunbeam-machine": SUNBEAM_MACHINE_CHANNEL,
    "sunbeam-clusterd": SUNBEAM_CLUSTERD_CHANNEL,
}


K8S_CHARMS: dict[str, str] = {}
K8S_CHARMS |= OPENSTACK_CHARMS_K8S
K8S_CHARMS |= OVN_CHARMS_K8S
K8S_CHARMS |= MYSQL_CHARMS_K8S
K8S_CHARMS |= MISC_CHARMS_K8S

MANIFEST_CHARM_VERSIONS: dict[str, str] = {}
MANIFEST_CHARM_VERSIONS |= K8S_CHARMS
MANIFEST_CHARM_VERSIONS |= MACHINE_CHARMS


# <TF plan>: <TF Plan dir>
TERRAFORM_DIR_NAMES = {
    "sunbeam-machine-plan": "deploy-sunbeam-machine",
    "microk8s-plan": "deploy-microk8s",
    "k8s-plan": "deploy-k8s",
    "microceph-plan": "deploy-microceph",
    "openstack-plan": "deploy-openstack",
    "hypervisor-plan": "deploy-openstack-hypervisor",
    "demo-setup": "demo-setup",
}


"""
Format of MANIFEST_ATTRIBUTES_TFVAR_MAP
{
    <plan>: {
        "charms": {
            <charm name>: {
                <CharmManifest Attrbiute>: <Terraform variable name>
                ...
                ...
            },
            ...
        },
        "caas_config": {
            <CaasConfig Attribute>: <Terraform variable name>
            ...
            ...
        },
    },
    ...
}

Example:
{
    "openstack-plan": {
        "charms": {
            "keystone-k8s": {
                "channel": "keystone-channel",
                "revision": "keystone-revision",
                "config": "keystone-config"
            },
        },
    },
    "k8s-plan": {
        "charms": {
            "k8s": {
                "channel": "k8s-channel",
                "revision": "k8s-revision",
                "config": "k8s-config",
            },
        },
    },
    "caas-setup": {
        "caas_config": {
            "image_name": "image-name",
            "image_url": "image-source-url"
        }
    }
}
"""
DEPLOY_OPENSTACK_TFVAR_MAP = {
    "charms": {
        charm: {
            "channel": f"{charm.removesuffix('-k8s')}-channel",
            "revision": f"{charm.removesuffix('-k8s')}-revision",
            "config": f"{charm.removesuffix('-k8s')}-config",
        }
        for charm, channel in K8S_CHARMS.items()
    }
}

# mysql-k8s supports a config map when deployed in many-mysql mode
DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["mysql-k8s"]["config-map"] = "mysql-config-map"
# mysql-k8s supports a storage map when deployed in many-mysql mode
DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["mysql-k8s"]["storage-map"] = "mysql-storage-map"
# mysql-k8s storage directive when deployed in single mode
DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["mysql-k8s"]["storage"] = "mysql-storage"
# glance-k8s storage directive
DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["glance-k8s"]["storage"] = "glance-storage"

DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["self-signed-certificates"] = {
    "channel": "certificate-authority-channel",
    "revision": "certificate-authority-revision",
    "config": "certificate-authority-config",
}
DEPLOY_MICROK8S_TFVAR_MAP = {
    "charms": {
        "microk8s": {
            "channel": "charm_microk8s_channel",
            "revision": "charm_microk8s_revision",
            "config": "charm_microk8s_config",
        }
    }
}
DEPLOY_K8S_TFVAR_MAP = {
    "charms": {
        "k8s": {
            "channel": "k8s_channel",
            "revision": "k8s_revision",
            "config": "k8s_config",
        },
    }
}
DEPLOY_MICROCEPH_TFVAR_MAP = {
    "charms": {
        "microceph": {
            "channel": "charm_microceph_channel",
            "revision": "charm_microceph_revision",
            "config": "charm_microceph_config",
        }
    }
}
DEPLOY_OPENSTACK_HYPERVISOR_TFVAR_MAP = {
    "charms": {
        "openstack-hypervisor": {
            "channel": "charm_channel",
            "revision": "charm_revision",
            "config": "charm_config",
        }
    }
}
DEPLOY_SUNBEAM_MACHINE_TFVAR_MAP = {
    "charms": {
        "sunbeam-machine": {
            "channel": "charm_channel",
            "revision": "charm_revision",
            "config": "charm_config",
        }
    }
}


MANIFEST_ATTRIBUTES_TFVAR_MAP = {
    "sunbeam-machine-plan": DEPLOY_SUNBEAM_MACHINE_TFVAR_MAP,
    "microk8s-plan": DEPLOY_MICROK8S_TFVAR_MAP,
    "k8s-plan": DEPLOY_K8S_TFVAR_MAP,
    "microceph-plan": DEPLOY_MICROCEPH_TFVAR_MAP,
    "openstack-plan": DEPLOY_OPENSTACK_TFVAR_MAP,
    "hypervisor-plan": DEPLOY_OPENSTACK_HYPERVISOR_TFVAR_MAP,
}
