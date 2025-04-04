# Using from a locally built snap

For testing a local change, you may wish to build and deploy the snap yourself.
There are some extra manual steps compared with installing from the snap store,
so this document details these.


## Build the snap

Run `snapcraft` from the root of the repository to build the `openstack` snap and save it in the working directory.  If you haven't run snapcraft before, please see the [Create a new snap](https://snapcraft.io/docs/create-a-new-snap) tutorial to get started.

```
$ snapcraft
Generated snap metadata
Created snap package openstack_2024.1_amd64.snap
```

This snap file can be copied to the machine where you are going to deploy it.

## Install and configure the snap

On the machine where you are going to deploy MicroStack.

Install in dangerous mode (this is required because it's an unsigned snap package):

```
sudo snap install --dangerous openstack_2024.1_amd64.snap
```

Auto aliases are not configured in dangerous mode,
so set up the sunbeam alias for convenience now:

```
sudo snap alias openstack.sunbeam sunbeam
```

Prepare the node:

```
sunbeam prepare-node-script --bootstrap | bash -x && newgrp snap_daemon
```

Because it's installed in dangerous mode, snap connections aren't added automatically.  This must be done after installing the Juju snap (Juju is installed by running the prepare node script above), and before we begin bootstrapping.  So add the connections now:

```
sudo snap connections openstack
sudo snap connect openstack:juju-bin juju:juju-bin
sudo snap connect openstack:dot-local-share-juju
sudo snap connect openstack:dot-config-openstack
```

## Continue deployment

Now the snap is configured and ready to go as usual, the same as if it were deployed from the official snap.  From here, you can continue the deployment from the bootstrap step.  See the [Get started with OpenStack](https://canonical-openstack.readthedocs-hosted.com/en/latest/tutorial/get-started-with-openstack/) tutorial for an example.

## Troubleshooting

If you see permission errors relating to files under /snap/openstack/ during the bootstrap process - for example:

```
$ openstack.sunbeam -v cluster bootstrap
...
                    PermissionError: [Errno 13] Permission denied: '/snap/openstack/x1/etc/deploy-sunbeam-machine'
           WARNING  An unexpected error has occurred. Please run 'sunbeam inspect' to generate an inspection report.                                              utils.py:290
           ERROR    Error: [Errno 13] Permission denied: '/snap/openstack/x1/etc/deploy-sunbeam-machine'                                                          utils.py:291
```

Then you may need to update the file permissions in your checked out copy of the source repository, and rebuild the snap.  Perhaps your machine had a customised umask set?  See [canonical/snapcraft#4658](https://github.com/canonical/snapcraft/issues/4658) for more information.
