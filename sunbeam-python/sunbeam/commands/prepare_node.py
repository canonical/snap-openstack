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

import click
from rich.console import Console

from sunbeam.versions import JUJU_CHANNEL, LXD_CHANNEL, SUPPORTED_RELEASE

console = Console()


PREPARE_NODE_TEMPLATE = f"""[ $(lsb_release -sc) != '{SUPPORTED_RELEASE}' ] && \
{{ echo 'ERROR: Sunbeam deploy only supported on {SUPPORTED_RELEASE}'; exit 1; }}

# :warning: Node Preparation for OpenStack Sunbeam :warning:
# All of these commands perform privileged operations
# please review carefully before execution.
USER=$(whoami)

if [ $(id -u) -eq 0 -o "$USER" = root ]; then
    cat << EOF
ERROR: Node Preparation script for OpenStack Sunbeam must be executed by
       non-root user with sudo permissions.
EOF
    exit 1
fi

# Check if user has passwordless sudo permissions and setup if need be
SUDO_ASKPASS=/bin/false sudo -A whoami &> /dev/null &&
sudo grep -r $USER /etc/{{sudoers,sudoers.d}} | grep NOPASSWD:ALL &> /dev/null || {{
    echo "$USER ALL=(ALL) NOPASSWD:ALL" > /tmp/90-$USER-sudo-access
    sudo install -m 440 /tmp/90-$USER-sudo-access /etc/sudoers.d/90-$USER-sudo-access
    rm -f /tmp/90-$USER-sudo-access
}}

# Ensure OpenSSH server is installed
dpkg -s openssh-server &> /dev/null || {{
    sudo apt install -y openssh-server
}}

# Ensure Curl is installed on Ubuntu Desktop
dpkg -s curl &> /dev/null || {{
    sudo apt install -y curl
}}

# Add $USER to the snap_daemon group supporting interaction
# with the sunbeam clustering daemon for cluster operations.
sudo usermod --append --groups snap_daemon $USER

# Generate keypair and set-up prompt-less access to local machine
[ -f $HOME/.ssh/id_ed25519 ] || ssh-keygen -f $HOME/.ssh/id_ed25519 -t ed25519 -N ""
cat $HOME/.ssh/id_ed25519.pub >> $HOME/.ssh/authorized_keys
ssh-keyscan -H $(hostname --all-ip-addresses) >> $HOME/.ssh/known_hosts

if ! grep -E 'HTTPS?_PROXY' /etc/environment &> /dev/null && \
! curl -s -m 10 -x "" api.charmhub.io &> /dev/null; then
    cat << EOF
ERROR: No external connectivity. Set HTTP_PROXY, HTTPS_PROXY, NO_PROXY
       in /etc/environment and re-run this command.
EOF
    exit 1
fi
"""

COMMON_TEMPLATE = f"""
# Connect snap to the ssh-keys interface to allow
# read access to private keys - this supports bootstrap
# of the Juju controller to the local machine via SSH.
# This also gives access to the ssh binary to the snap.
sudo snap connect openstack:ssh-keys

# Install the Juju snap
sudo snap install --channel {JUJU_CHANNEL} juju

# Workaround a bug between snapd and juju
mkdir -p $HOME/.local/share
mkdir -p $HOME/.config/openstack

# Check the snap channel and deduce risk level from it
snap_output=$(snap list openstack --unicode=never --color=never | grep openstack)
track=$(awk -v col=4 '{{print $col}}' <<<"$snap_output")

# if never installed from the store, the channel is "-"
if [[ $track =~ "edge" ]] || [[ $track == "-" ]]; then
    risk="edge"
elif [[ $track =~ "beta" ]]; then
    risk="beta"
elif [[ $track =~ "candidate" ]]; then
    risk="candidate"
else
    risk="stable"
fi

if [[ $risk != "stable" ]]; then
    sudo snap set openstack deployment.risk=$risk
    echo "Snap has been automatically configured to deploy from" \
        "$risk channel."
    echo "Override by passing a custom manifest with -m/--manifest."
fi
"""

BOOTSTRAP_TEMPLATE = f"""
# Install the lxd snap
sudo snap install lxd --channel {LXD_CHANNEL}
USER=$(whoami)
# Ensure current user is part of the LXD group
sudo usermod --append --groups lxd $USER

if [ -n "$(sudo --user $USER lxc network list --format csv | grep lxdbr0)" ]; then
    echo 'Sunbeam requires the LXD bridge to be called anything except lxdbr0'
    exit 1
fi

# Try to determine if LXD is already bootstrapped
if [ -z "$(sudo --user $USER lxc storage list --format csv)" ];
then
    echo 'Bootstrapping LXD'
    cat <<EOF | sudo --user $USER lxd init --preseed
networks:
- config:
    ipv4.address: auto
    ipv6.address: none
  name: sunbeambr0
  project: default
storage_pools:
- name: default
  driver: dir
profiles:
- devices:
    eth0:
      name: eth0
      network: sunbeambr0
      type: nic
    root:
      path: /
      pool: default
      type: disk
  name: default
EOF
fi
# Bootstrap juju onto LXD
echo 'Bootstrapping Juju onto LXD'
sudo --user $USER juju show-controller 2>/dev/null
if [ $? -ne 0 ]; then
    sudo --user $USER juju bootstrap localhost
fi
"""


@click.command()
@click.option(
    "--bootstrap",
    is_flag=True,
    help="Prepare the node for use as primary node.",
    default=False,
)
@click.option(
    "--client",
    "-c",
    is_flag=True,
    help="Prepare the node for use as a client.",
    default=False,
)
def prepare_node_script(bootstrap: bool = False, client: bool = False) -> None:
    """Generate script to prepare a node for Sunbeam use."""
    if bootstrap and client:
        raise click.UsageError("Cannot prepare node as both client and bootstrap")
    script = "#!/bin/bash\n"
    if not client:
        script += PREPARE_NODE_TEMPLATE
    script += COMMON_TEMPLATE
    if bootstrap:
        script += BOOTSTRAP_TEMPLATE
    console.print(script, soft_wrap=True)
