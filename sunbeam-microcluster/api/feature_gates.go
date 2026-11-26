package api

import (
	"encoding/json"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v3/microcluster/types"
	"github.com/gorilla/mux"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/feature-gates endpoint.
var featureGatesCmd = types.Endpoint{
	Path: "feature-gates",

	Get:  access.ClusterCATrustedEndpoint(cmdFeatureGatesGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdFeatureGatesPost, true),
}

// /1.0/feature-gates/<gate-key> endpoint.
var featureGateCmd = types.Endpoint{
	Path: "feature-gates/{gatekey}",

	Get:    access.ClusterCATrustedEndpoint(cmdFeatureGateGet, true),
	Delete: access.ClusterCATrustedEndpoint(cmdFeatureGateDelete, true),
	Put:    access.ClusterCATrustedEndpoint(cmdFeatureGatePut, true),
}

func cmdFeatureGatesGetAll(s types.State, r *http.Request) types.Response {
	gates, err := sunbeam.ListFeatureGates(r.Context(), s)
	if err != nil {
		return types.InternalError(err)
	}

	return types.SyncResponse(true, gates)
}

func cmdFeatureGatesPost(s types.State, r *http.Request) types.Response {
	var req apitypes.FeatureGate

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.AddFeatureGate(r.Context(), s, req.GateKey, req.Enabled)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdFeatureGateGet(s types.State, r *http.Request) types.Response {
	gateKey, err := url.PathUnescape(mux.Vars(r)["gatekey"])
	if err != nil {
		return types.InternalError(err)
	}

	gate, err := sunbeam.GetFeatureGate(r.Context(), s, gateKey)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.SyncResponse(true, gate)
}

func cmdFeatureGateDelete(s types.State, r *http.Request) types.Response {
	gateKey, err := url.PathUnescape(mux.Vars(r)["gatekey"])
	if err != nil {
		return types.SmartError(err)
	}

	err = sunbeam.DeleteFeatureGate(r.Context(), s, gateKey)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdFeatureGatePut(s types.State, r *http.Request) types.Response {
	gateKey, err := url.PathUnescape(mux.Vars(r)["gatekey"])
	if err != nil {
		return types.SmartError(err)
	}

	var req apitypes.FeatureGate
	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.UpdateFeatureGate(r.Context(), s, gateKey, req.Enabled)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}
