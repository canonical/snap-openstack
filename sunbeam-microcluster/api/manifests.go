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

// /1.0/manifests endpoint.
var manifestsCmd = types.Endpoint{
	Path: "manifests",

	Get:  access.ClusterCATrustedEndpoint(cmdManifestsGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdManifestsPost, true),
}

// /1.0/manifests/<manifestid> endpoint.
// /1.0/manifests/latest will give the latest inserted manifest record
var manifestCmd = types.Endpoint{
	Path: "manifests/{manifestid}",

	Get:    access.ClusterCATrustedEndpoint(cmdManifestGet, true),
	Delete: access.ClusterCATrustedEndpoint(cmdManifestDelete, true),
}

func cmdManifestsGetAll(s types.State, r *http.Request) types.Response {

	manifests, err := sunbeam.ListManifests(r.Context(), s)
	if err != nil {
		return types.InternalError(err)
	}

	return types.SyncResponse(true, manifests)
}

func cmdManifestGet(s types.State, r *http.Request) types.Response {
	var manifestid string
	manifestid, err := url.PathUnescape(mux.Vars(r)["manifestid"])
	if err != nil {
		return types.InternalError(err)
	}
	manifest, err := sunbeam.GetManifest(r.Context(), s, manifestid)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.SyncResponse(true, manifest)
}

func cmdManifestsPost(s types.State, r *http.Request) types.Response {
	var req apitypes.Manifest

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.AddManifest(r.Context(), s, req.ManifestID, req.Data)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdManifestDelete(s types.State, r *http.Request) types.Response {
	manifestid, err := url.PathUnescape(mux.Vars(r)["manifestid"])
	if err != nil {
		return types.SmartError(err)
	}
	err = sunbeam.DeleteManifest(r.Context(), s, manifestid)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}
