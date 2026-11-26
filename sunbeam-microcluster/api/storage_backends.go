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

// /1.0/storage-backend endpoint.
var storageBackendsCmd = types.Endpoint{
	Path: "storage-backend",

	Get:  access.ClusterCATrustedEndpoint(cmdStorageBackendsGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdStorageBackendsPost, true),
}

// /1.0/storage-backend/<backend-name> endpoint.
var storageBackendCmd = types.Endpoint{
	Path: "storage-backend/{backendname}",

	Get:    access.ClusterCATrustedEndpoint(cmdStorageBackendGet, true),
	Delete: access.ClusterCATrustedEndpoint(cmdStorageBackendDelete, true),
	Put:    access.ClusterCATrustedEndpoint(cmdStorageBackendPut, true),
}

func cmdStorageBackendsGetAll(s types.State, r *http.Request) types.Response {

	storageBackends, err := sunbeam.ListStorageBackends(r.Context(), s)
	if err != nil {
		return types.InternalError(err)
	}

	return types.SyncResponse(true, storageBackends)
}

func cmdStorageBackendsPost(s types.State, r *http.Request) types.Response {
	var req apitypes.StorageBackend

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.AddStorageBackend(r.Context(), s, req.Name, req.Type, req.Principal, req.ModelUUID, req.Config)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdStorageBackendGet(s types.State, r *http.Request) types.Response {
	var backendName string
	backendName, err := url.PathUnescape(mux.Vars(r)["backendname"])
	if err != nil {
		return types.InternalError(err)
	}
	backend, err := sunbeam.GetStorageBackend(r.Context(), s, backendName)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.SyncResponse(true, backend)
}

func cmdStorageBackendDelete(s types.State, r *http.Request) types.Response {
	backendName, err := url.PathUnescape(mux.Vars(r)["backendname"])
	if err != nil {
		return types.SmartError(err)
	}
	err = sunbeam.DeleteStorageBackend(r.Context(), s, backendName)
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

func cmdStorageBackendPut(s types.State, r *http.Request) types.Response {
	backendName, err := url.PathUnescape(mux.Vars(r)["backendname"])
	if err != nil {
		return types.SmartError(err)
	}

	var req apitypes.StorageBackend
	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.UpdateStorageBackend(r.Context(), s, backendName, req.Type, req.Principal, req.ModelUUID, req.Config)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}
