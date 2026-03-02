package api

import (
	"encoding/json"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v2/rest"
	"github.com/canonical/microcluster/v2/state"
	"github.com/gorilla/mux"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/feature-gates endpoint.
var featureGatesCmd = rest.Endpoint{
	Path: "feature-gates",

	Get:  access.ClusterCATrustedEndpoint(cmdFeatureGatesGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdFeatureGatesPost, true),
}

// /1.0/feature-gates/<gate-key> endpoint.
var featureGateCmd = rest.Endpoint{
	Path: "feature-gates/{gatekey}",

	Get:    access.ClusterCATrustedEndpoint(cmdFeatureGateGet, true),
	Delete: access.ClusterCATrustedEndpoint(cmdFeatureGateDelete, true),
	Put:    access.ClusterCATrustedEndpoint(cmdFeatureGatePut, true),
}

func cmdFeatureGatesGetAll(s state.State, r *http.Request) response.Response {
	gates, err := sunbeam.ListFeatureGates(r.Context(), s)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, gates)
}

func cmdFeatureGatesPost(s state.State, r *http.Request) response.Response {
	var req apitypes.FeatureGate

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.AddFeatureGate(r.Context(), s, req.GateKey, req.Enabled)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdFeatureGateGet(s state.State, r *http.Request) response.Response {
	gateKey, err := url.PathUnescape(mux.Vars(r)["gatekey"])
	if err != nil {
		return response.InternalError(err)
	}

	gate, err := sunbeam.GetFeatureGate(r.Context(), s, gateKey)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, gate)
}

func cmdFeatureGateDelete(s state.State, r *http.Request) response.Response {
	gateKey, err := url.PathUnescape(mux.Vars(r)["gatekey"])
	if err != nil {
		return response.SmartError(err)
	}

	err = sunbeam.DeleteFeatureGate(r.Context(), s, gateKey)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdFeatureGatePut(s state.State, r *http.Request) response.Response {
	gateKey, err := url.PathUnescape(mux.Vars(r)["gatekey"])
	if err != nil {
		return response.SmartError(err)
	}

	var req apitypes.FeatureGate
	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.UpdateFeatureGate(r.Context(), s, gateKey, req.Enabled)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
