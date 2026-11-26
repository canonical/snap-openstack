// Package api provides the REST API endpoints.
package api

import (
	"github.com/canonical/microcluster/v3/microcluster/types"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
)

// Servers is a global list of all API servers on the /1.0 endpoint of
// microcluster.
var Servers = map[string]types.Server{
	"sunbeam": {
		CoreAPI:   true,
		ServeUnix: true,
		Resources: []types.Resources{
			{
				PathPrefix: apitypes.ExtendedPathPrefix,
				Endpoints: []types.Endpoint{
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
					storageBackendsCmd,
					storageBackendCmd,
					featureGatesCmd,
					featureGateCmd,
				},
			},
			{
				PathPrefix: apitypes.LocalPathPrefix,
				Endpoints: []types.Endpoint{
					certPair,
				},
			},
		},
	},
}
