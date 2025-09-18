// Package api provides the REST API endpoints.
package api

import (
	"github.com/canonical/microcluster/v2/rest"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
)

// Servers is a global list of all API servers on the /1.0 endpoint of
// microcluster.
var Servers = map[string]rest.Server{
	"sunbeam": {
		CoreAPI:   true,
		ServeUnix: true,
		Resources: []rest.Resources{
			{
				PathPrefix: apitypes.ExtendedPathPrefix,
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
				PathPrefix: apitypes.LocalPathPrefix,
				Endpoints: []rest.Endpoint{
					certPair,
				},
			},
		},
	},
}
