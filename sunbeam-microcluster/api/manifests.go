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

// /1.0/manifests endpoint.
var manifestsCmd = rest.Endpoint{
	Path: "manifests",

	Get:  access.ClusterCATrustedEndpoint(cmdManifestsGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdManifestsPost, true),
}

// /1.0/manifests/<manifestid> endpoint.
// /1.0/manifests/latest will give the latest inserted manifest record
var manifestCmd = rest.Endpoint{
	Path: "manifests/{manifestid}",

	Get:    access.ClusterCATrustedEndpoint(cmdManifestGet, true),
	Delete: access.ClusterCATrustedEndpoint(cmdManifestDelete, true),
}

func cmdManifestsGetAll(s state.State, r *http.Request) response.Response {

	manifests, err := sunbeam.ListManifests(r.Context(), s)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, manifests)
}

func cmdManifestGet(s state.State, r *http.Request) response.Response {
	var manifestid string
	manifestid, err := url.PathUnescape(mux.Vars(r)["manifestid"])
	if err != nil {
		return response.InternalError(err)
	}
	manifest, err := sunbeam.GetManifest(r.Context(), s, manifestid)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, manifest)
}

func cmdManifestsPost(s state.State, r *http.Request) response.Response {
	var req apitypes.Manifest

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.AddManifest(r.Context(), s, req.ManifestID, req.Data)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdManifestDelete(s state.State, r *http.Request) response.Response {
	manifestid, err := url.PathUnescape(mux.Vars(r)["manifestid"])
	if err != nil {
		return response.SmartError(err)
	}
	err = sunbeam.DeleteManifest(r.Context(), s, manifestid)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
