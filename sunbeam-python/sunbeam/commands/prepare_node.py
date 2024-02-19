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

console = Console()


JUJU_CHANNEL = "3.2/stable"
SUPPORTED_RELEASE = "jammy"

PREPARE_NODE_TEMPLATE = f"""#!/bin/bash

[ $(lsb_release -sc) != '{SUPPORTED_RELEASE}' ] && \
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

# Increase the number of inotify watchers per user
echo "fs.inotify.max_user_instances = 1024" | sudo tee /etc/sysctl.d/80-sunbeam.conf
sudo sysctl -q -p /etc/sysctl.d/80-sunbeam.conf

# Connect snap to the ssh-keys interface to allow
# read access to private keys - this supports bootstrap
# of the Juju controller to the local machine via SSH.
sudo snap connect openstack:ssh-keys

# Add $USER to the snap_daemon group and adopt new permissions
# supporting interaction with the sunbeam clustering daemon for
# cluster operations.
sudo addgroup $USER snap_daemon
newgrp snap_daemon

# Generate keypair and set-up prompt-less access to local machine
[ -f $HOME/.ssh/id_rsa_snap-openstack ] || \
  ssh-keygen -b 4096 -f $HOME/.ssh/id_rsa_snap-openstack -t rsa -N ""
cat $HOME/.ssh/id_rsa_snap-openstack.pub >> $HOME/.ssh/authorized_keys
ssh-keyscan -H $(hostname --all-ip-addresses) >> $HOME/.ssh/known_hosts
[ -f $HOME/.ssh/config ] || \
  ( touch $HOME/.ssh/config ; chmod 0600 $HOME/.ssh/config )
cat >> $HOME/.ssh/config <<EOF
Host $( hostname --all-ip-addresses | paste -s )
  IdentityFile ~/.ssh/id_rsa_snap-openstack
EOF

# Install the Juju snap
sudo snap install --channel {JUJU_CHANNEL} juju

# Workaround a bug between snapd and juju
mkdir -p $HOME/.local/share
mkdir -p $HOME/.config/openstack
"""


@click.command()
def prepare_node_script() -> None:
    """Generate script to prepare a node for Sunbeam use."""
    console.print(PREPARE_NODE_TEMPLATE, soft_wrap=True)
