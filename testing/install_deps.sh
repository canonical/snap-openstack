#!/bin/bash -x

if [ "x$(which terragrunt)" != "x0" ]; then
    sudo wget -O /usr/local/bin/terragrunt https://github.com/gruntwork-io/terragrunt/releases/download/v0.87.1/terragrunt_linux_amd64
    sudo chmod +x /usr/local/bin/terragrunt
fi

# install opentofu
if [ "x$(which tofu)" != "x0" ]; then
    sudo apt-get update
    sudo apt-get install -y apt-transport-https ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://get.opentofu.org/opentofu.gpg | sudo tee /etc/apt/keyrings/opentofu.gpg >/dev/null
    curl -fsSL https://packages.opentofu.org/opentofu/tofu/gpgkey | sudo gpg --no-tty --batch --dearmor -o /etc/apt/keyrings/opentofu-repo.gpg >/dev/null
    sudo chmod a+r /etc/apt/keyrings/opentofu.gpg /etc/apt/keyrings/opentofu-repo.gpg
echo \
  "deb [signed-by=/etc/apt/keyrings/opentofu.gpg,/etc/apt/keyrings/opentofu-repo.gpg] https://packages.opentofu.org/opentofu/tofu/any/ any main
deb-src [signed-by=/etc/apt/keyrings/opentofu.gpg,/etc/apt/keyrings/opentofu-repo.gpg] https://packages.opentofu.org/opentofu/tofu/any/ any main" | \
  sudo tee /etc/apt/sources.list.d/opentofu.list > /dev/null

    sudo chmod a+r /etc/apt/sources.list.d/opentofu.list
    sudo apt-get update
    sudo apt-get install -y -qq tofu
fi

# install terraform
if [ "x$(which tofu)" != "x0" ]; then
    sudo apt-get update -q
    sudo apt-get install -yq gnupg software-properties-common
    wget -O- https://apt.releases.hashicorp.com/gpg | \
        gpg --dearmor | \
        sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg > /dev/null
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release || lsb_release -cs) main" | \
        sudo tee /etc/apt/sources.list.d/hashicorp.list
    sudo apt-get update -q
    sudo apt-get install -yq terraform
fi

if [ "x$(which virsh)" != "x0" ]; then
    sudo apt-get install -y -qq \
        libvirt-daemon \
        libvirt-daemon-driver-qemu \
        libvirt-daemon-system \
        libvirt-clients
    sudo sed -i '/^security_driver/d' /etc/libvirt/qemu.conf
    echo 'security_driver = "none"' | sudo tee -a /etc/libvirt/qemu.conf >/dev/null
    sudo systemctl restart libvirtd
fi
