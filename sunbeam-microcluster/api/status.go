// Package api provides the REST API endpoints.
package api

import (
	"context"
	"net/http"
	"time"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/microcluster/rest"
	"github.com/canonical/microcluster/state"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/lxd/shared/logger"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
)

var statusCmd = rest.Endpoint{
	Path: "status",

	Get: access.ClusterCATrustedEndpoint(cmdGetStatus, false),
}

func cmdGetStatus(s *state.State, _ *http.Request) response.Response {
	leader, err := s.Leader()

	if err != nil {
		logger.Errorf("Failed to get leader client: %v", err)
		return response.InternalError(err)
	}

	queryCtx, cancel := context.WithTimeout(s.Context, time.Second*30)
	defer cancel()

	var data []map[string]interface{}
	err = leader.Query(queryCtx, "GET", "cluster/1.0", api.NewURL().Path("cluster"), nil, &data)
	if err != nil {
		logger.Errorf("Failed to get cluster status: %v", err)
		return response.InternalError(err)
	}

	return response.SyncResponse(true, data)
}
