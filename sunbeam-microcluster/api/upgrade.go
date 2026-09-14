// Package api provides the REST API endpoints.
package api

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v2/rest"
	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/upgrade/lock endpoint.
var upgradeLockCmd = rest.Endpoint{
	Path: "upgrade/lock",

	Post:   access.ClusterCATrustedEndpoint(cmdUpgradeLockAcquire, true),
	Put:    access.ClusterCATrustedEndpoint(cmdUpgradeLockRefresh, true),
	Delete: access.ClusterCATrustedEndpoint(cmdUpgradeLockRelease, true),
}

// /1.0/upgrade/state endpoint.
var upgradeStateCmd = rest.Endpoint{
	Path: "upgrade/state",

	Get: access.ClusterCATrustedEndpoint(cmdUpgradeStateGet, true),
	Put: access.ClusterCATrustedEndpoint(cmdUpgradeStatePut, true),
}

// /1.0/upgrade/active endpoint.
var upgradeActiveCmd = rest.Endpoint{
	Path: "upgrade/active",

	Get: access.ClusterCATrustedEndpoint(cmdUpgradeActiveGet, true),
}

func cmdUpgradeLockAcquire(s state.State, r *http.Request) response.Response {
	var req apitypes.AcquireUpgradeLockRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		return response.BadRequest(err)
	}
	if req.HolderID == "" {
		return response.BadRequest(fmt.Errorf("holder_id is required"))
	}
	token, err := sunbeam.AcquireUpgradeLock(r.Context(), s, req.HolderID)
	if err != nil {
		var held *database.LockHeldError
		if api.StatusErrorCheck(err, http.StatusConflict) {
			return response.Conflict(err)
		}
		if errors.As(err, &held) {
			return response.Conflict(err)
		}
		return response.InternalError(err)
	}
	return response.SyncResponse(true, apitypes.AcquireUpgradeLockResponse{Token: token})
}

func cmdUpgradeLockRefresh(s state.State, r *http.Request) response.Response {
	var req apitypes.RefreshUpgradeLockRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		return response.BadRequest(err)
	}
	if err := sunbeam.RefreshUpgradeLock(r.Context(), s, req.Token); err != nil {
		return tokenErrorResponse(err)
	}
	return response.EmptySyncResponse
}

func cmdUpgradeLockRelease(s state.State, r *http.Request) response.Response {
	var req apitypes.ReleaseUpgradeLockRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		return response.BadRequest(err)
	}
	if err := sunbeam.ReleaseUpgradeLock(r.Context(), s, req.Token); err != nil {
		return tokenErrorResponse(err)
	}
	return response.EmptySyncResponse
}

func cmdUpgradeStateGet(s state.State, r *http.Request) response.Response {
	stateJSON, err := sunbeam.GetUpgradeState(r.Context(), s)
	if err != nil {
		if api.StatusErrorCheck(err, http.StatusNotFound) {
			return response.NotFound(err)
		}
		return response.InternalError(err)
	}
	return response.SyncResponse(true, stateJSON)
}

func cmdUpgradeStatePut(s state.State, r *http.Request) response.Response {
	var req apitypes.UpdateUpgradeStateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		return response.BadRequest(err)
	}
	if err := sunbeam.UpdateUpgradeState(r.Context(), s, req.Token, req.State); err != nil {
		return tokenErrorResponse(err)
	}
	return response.EmptySyncResponse
}

func cmdUpgradeActiveGet(s state.State, r *http.Request) response.Response {
	active, err := sunbeam.IsUpgradeActive(r.Context(), s)
	if err != nil {
		return response.InternalError(err)
	}
	return response.SyncResponse(true, apitypes.IsUpgradeActiveResponse{Active: active})
}

// tokenErrorResponse maps a database.TokenMismatchError to HTTP 409 Conflict
// (stale fencing token — the caller's lock expired and was re-acquired).
func tokenErrorResponse(err error) response.Response {
	var mismatch *database.TokenMismatchError
	if errors.As(err, &mismatch) {
		return response.Conflict(err)
	}
	return response.InternalError(err)
}
