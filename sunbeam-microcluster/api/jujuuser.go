package api

import (
	"encoding/json"
	"errors"
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

// /1.0/jujuusers endpoint.
var jujuusersCmd = rest.Endpoint{
	Path: "jujuusers",

	Get:  access.ClusterCATrustedEndpoint(cmdJujuUsersGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdJujuUsersPost, true),
}

// /1.0/jujuusers/<name> endpoint.
var jujuuserCmd = rest.Endpoint{
	Path: "jujuusers/{name}",

	Get:    access.ClusterCATrustedEndpoint(cmdJujuUsersGet, true),
	Put:    access.ClusterCATrustedEndpoint(cmdJujuUsersPut, true),
	Delete: access.ClusterCATrustedEndpoint(cmdJujuUsersDelete, true),
}

func cmdJujuUsersGetAll(s state.State, r *http.Request) response.Response {
	users, err := sunbeam.ListJujuUsers(r.Context(), s)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, users)
}

func cmdJujuUsersGet(s state.State, r *http.Request) response.Response {
	var name string
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.InternalError(err)
	}
	jujuUser, err := sunbeam.GetJujuUser(r.Context(), s, name)
	if err != nil {
		// Return the appropriate error if juju user is not found
		var statusErr api.StatusError
		if errors.As(err, &statusErr) {
			if statusErr.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, jujuUser)
}

func cmdJujuUsersPost(s state.State, r *http.Request) response.Response {
	var req apitypes.JujuUser

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.AddJujuUser(r.Context(), s, req.Username, req.Token)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdJujuUsersPut(s state.State, r *http.Request) response.Response {
	var req apitypes.JujuUser

	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.SmartError(err)
	}

	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.UpdateJujuUser(r.Context(), s, name, req.Token)
	if err != nil {
		// Return the appropriate error if juju user is not found
		var statusErr api.StatusError
		if errors.As(err, &statusErr) {
			if statusErr.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdJujuUsersDelete(s state.State, r *http.Request) response.Response {
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.SmartError(err)
	}
	err = sunbeam.DeleteJujuUser(r.Context(), s, name)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
