#!/bin/bash -x

if [ "x$(which terragrunt)" != "x0" ]; then
    sudo wget -O /usr/local/bin/terragrunt https://github.com/gruntwork-io/terragrunt/releases/download/v0.87.1/terragrunt_linux_amd64
    chmod +x /usr/local/bin/terragrunt
fi

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

if [ "x$(which virsh)" != "x0" ]; then
    sudo apt-get install -y -qq \
        libvirt-daemon \
        libvirt-daemon-driver-qemu \
        libvirt-daemon-system \
        libvirt-clients
    sudo sed '/^security_driver/d' /etc/libvirt/qemu.conf
    echo 'security_driver = "none"' | sudo tee -a /etc/libvirt/qemu.conf
    sudo systemctl restart libvirtd
fi
