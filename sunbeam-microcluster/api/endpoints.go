// Package api provides the REST API endpoints.
package api

import (
	"github.com/canonical/microcluster/rest"
)

// Endpoints is a global list of all API endpoints on the /1.0 endpoint of
// microcluster.
var Endpoints = []rest.Endpoint{
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
}
