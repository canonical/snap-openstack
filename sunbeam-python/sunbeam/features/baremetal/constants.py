# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

IRONIC_CONDUCTOR_APP = "ironic-conductor"
IRONIC_APP_TIMEOUT = 600
NOVA_IRONIC_SHARDS_TFVAR = "ironic-compute-shards"
IRONIC_CONDUCTOR_GROUPS_TFVAR = "ironic-conductor-groups"
NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR = "netconf-conf-secrets"
NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR = "generic-conf-secrets"
NEUTRON_SWITCH_CONF_SECRETS_TFVAR = "switch-conf-secrets"

SWITCH_CONFIG_TFVAR = {
    "netconf": NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR,
    "generic": NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR,
}
