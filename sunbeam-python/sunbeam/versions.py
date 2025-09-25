# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0


from functools import cache
from typing import TypedDict


@cache
def determine_version() -> str:
    # This function could be expanded to determine version dynamically
    from snaphelpers import Snap

    try:
        snap = Snap()
        risk = str(snap.config.get("deployment.version"))
    except Exception:
        risk = "2024.1"
    return risk


SUPPORTED_RELEASE = "noble"
JUJU_CHANNEL = "3.6/stable"
JUJU_BASE = "ubuntu@24.04"
OPENSTACK_CHANNEL = f"{determine_version()}/stable"
OVN_CHANNEL = "24.03/stable"
RABBITMQ_CHANNEL = "3.12/stable"
TRAEFIK_CHANNEL = "latest/stable"
MICROCEPH_CHANNEL = "squid/stable"
MICROOVN_CHANNEL = "latest/edge"
MYSQL_CHANNEL = "8.0/stable"
CERT_AUTH_CHANNEL = "1/stable"
BIND_CHANNEL = "9/stable"
VAULT_CHANNEL = "1.16/stable"
CONSUL_CHANNEL = "1.19/edge"
K8S_CHANNEL = "1.32/stable"
LXD_CHANNEL = "5.21/stable"
SUNBEAM_EPA_ORCHESTRATOR_CHANNEL = "2024.1/edge"

CLUSTER_API_VERSIONS = {
    "cluster-api": "v1.10.5",
    "bootstrap-canonical-kubernetes": "v0.4.2",
    "control-plane-canonical-kubernetes": "v0.4.2",
    "infrastructure-openstack": "v0.12.4",
    "addon-helm": "v0.3.2",
}

# List of charms with default channels
OPENSTACK_CHARMS_K8S = {
    "cinder-k8s": OPENSTACK_CHANNEL,
    "glance-k8s": OPENSTACK_CHANNEL,
    "horizon-k8s": OPENSTACK_CHANNEL,
    "keystone-k8s": OPENSTACK_CHANNEL,
    "keystone-saml-k8s": OPENSTACK_CHANNEL,
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
    "microovn": MICROOVN_CHANNEL,
    "k8s": K8S_CHANNEL,
    "openstack-hypervisor": OPENSTACK_CHANNEL,
    "sunbeam-machine": OPENSTACK_CHANNEL,
    "sunbeam-clusterd": OPENSTACK_CHANNEL,
    "self-signed-certificates": CERT_AUTH_CHANNEL,
    "cinder-volume": OPENSTACK_CHANNEL,
    "cinder-volume-ceph": OPENSTACK_CHANNEL,
    "epa-orchestrator": SUNBEAM_EPA_ORCHESTRATOR_CHANNEL,
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
    "k8s-plan": "deploy-k8s",
    "microceph-plan": "deploy-microceph",
    "microovn-plan": "deploy-microovn",
    "cinder-volume-plan": "deploy-cinder-volume",
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
        "preserve": [
            <list of variables to preserve when reapplying the plan>
        ]
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


class VarMap(TypedDict, total=False):
    charms: dict[str, dict[str, str]]
    # When re-applying a plan, Sunbeam will remove variables that are in DB
    # but not in manifest / function call. Some variables are computed by
    # Sunbeam and will be missing from the manifest. Allow each plan to define
    # such variables that should not be removed when reapplying the plan.
    preserve: list[str]
    caas_config: dict[str, str]


DEPLOY_OPENSTACK_TFVAR_MAP: VarMap = {
    "charms": {
        charm: {
            "channel": f"{charm.removesuffix('-k8s')}-channel",
            "revision": f"{charm.removesuffix('-k8s')}-revision",
            "config": f"{charm.removesuffix('-k8s')}-config",
        }
        for charm, channel in K8S_CHARMS.items()
    },
    "preserve": [
        "mysql-config",
        "mysql-config-map",
        "mysql-storage",
        "mysql-storage-map",
    ],
}

# kratos-external-idp-integrator is used to enable external IDP integration.
DEPLOY_OPENSTACK_TFVAR_MAP["charms"]["kratos-external-idp-integrator"] = {
    "channel": "kratos-idp-channel",
    "revision": "kratos-idp-revision",
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
DEPLOY_K8S_TFVAR_MAP: VarMap = {
    "charms": {
        "k8s": {
            "channel": "k8s_channel",
            "revision": "k8s_revision",
            "config": "k8s_config",
        },
    }
}
DEPLOY_MICROCEPH_TFVAR_MAP: VarMap = {
    "charms": {
        "microceph": {
            "channel": "charm_microceph_channel",
            "revision": "charm_microceph_revision",
            "config": "charm_microceph_config",
        }
    }
}
DEPLOY_MICROOVN_TFVAR_MAP: VarMap = {
    "charms": {
        "microovn": {
            "channel": "charm_microovn_channel",
            "revision": "charm_microovn_revision",
            "config": "charm_microovn_config",
        }
    }
}
DEPLOY_OPENSTACK_HYPERVISOR_TFVAR_MAP: VarMap = {
    "charms": {
        "openstack-hypervisor": {
            "channel": "charm_channel",
            "revision": "charm_revision",
            "config": "charm_config",
        }
    }
}
DEPLOY_SUNBEAM_MACHINE_TFVAR_MAP: VarMap = {
    "charms": {
        "sunbeam-machine": {
            "channel": "charm_channel",
            "revision": "charm_revision",
            "config": "charm_config",
        },
        "epa-orchestrator": {
            "channel": "charm_epa_orchestrator_channel",
            "revision": "charm_epa_orchestrator_revision",
            "config": "charm_epa_orchestrator_config",
        },
    }
}
DEPLOY_CINDER_VOLUME_TFVAR_MAP: VarMap = {
    "charms": {
        "cinder-volume": {
            "channel": "charm_cinder_volume_channel",
            "revision": "charm_cinder_volume_revision",
            "config": "charm_cinder_volume_config",
        },
        "cinder-volume-ceph": {
            "channel": "charm_cinder_volume_ceph_channel",
            "revision": "charm_cinder_volume_ceph_revision",
            "config": "charm_cinder_volume_ceph_config",
        },
    }
}


MANIFEST_ATTRIBUTES_TFVAR_MAP: dict[str, VarMap] = {
    "sunbeam-machine-plan": DEPLOY_SUNBEAM_MACHINE_TFVAR_MAP,
    "k8s-plan": DEPLOY_K8S_TFVAR_MAP,
    "microceph-plan": DEPLOY_MICROCEPH_TFVAR_MAP,
    "microovn-plan": DEPLOY_MICROOVN_TFVAR_MAP,
    "openstack-plan": DEPLOY_OPENSTACK_TFVAR_MAP,
    "hypervisor-plan": DEPLOY_OPENSTACK_HYPERVISOR_TFVAR_MAP,
    "cinder-volume-plan": DEPLOY_CINDER_VOLUME_TFVAR_MAP,
}
