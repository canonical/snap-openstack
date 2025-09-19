# sunbeam-microcluster

The cluster daemon for Sunbeam based on MicroCluster library

# Building sunbeam-microcluster

The MicroCluster library makes use of dqlite which provides a raft based
sqlite compatible database for shared state across the Sunbeam cluster.

This requires a few dependencies to be installed:

    sudo add-apt-repository -y ppa:dqlite/dev
    sudo apt install gcc make libdqlite1.17-dev -y
    sudo snap install --channel 1.21 --classic go

after which is possible to build sunbeam-microcluster:

    make build

Have fun!
