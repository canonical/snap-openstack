// Package api provides the REST API endpoints.
package api

import (
	"github.com/canonical/microcluster/rest"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/types"
)

// Servers is a global list of all API servers on the /1.0 endpoint of
// microcluster.
var Servers = []rest.Server{
	{
		CoreAPI: true,
		Resources: []rest.Resources{
			{
				PathPrefix: types.ExtendedPathPrefix,
				Endpoints: []rest.Endpoint{
					nodesCmd,
					nodeCmd,
					terraformStateListCmd,
					terraformStateCmd,
					terraformLockListCmd,
					terraformLockCmd,
					terraformUnlockCmd,
					jujuusersCmd,
					jujuuserCmd,
					configCmd,
					manifestsCmd,
					manifestCmd,
					statusCmd,
				},
			},
			{
				PathPrefix: types.LocalPathPrefix,
				Endpoints: []rest.Endpoint{
					certPair,
				},
			},
		},
	},
}
