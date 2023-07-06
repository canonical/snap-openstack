package api

import (
	"encoding/json"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/rest"
	"github.com/canonical/microcluster/state"
	"github.com/gorilla/mux"

	"github.com/openstack-snaps/snap-openstack/sunbeam-microcluster/api/types"
	"github.com/openstack-snaps/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/jujuusers endpoint.
var jujuusersCmd = rest.Endpoint{
	Path: "jujuusers",

	Get:  rest.EndpointAction{Handler: cmdJujuUsersGetAll, ProxyTarget: true},
	Post: rest.EndpointAction{Handler: cmdJujuUsersPost, ProxyTarget: true},
}

// /1.0/jujuusers/<name> endpoint.
var jujuuserCmd = rest.Endpoint{
	Path: "jujuusers/{name}",

	Get:    rest.EndpointAction{Handler: cmdJujuUsersGet, ProxyTarget: true},
	Delete: rest.EndpointAction{Handler: cmdJujuUsersDelete, ProxyTarget: true},
}

func cmdJujuUsersGetAll(s *state.State, _ *http.Request) response.Response {
	users, err := sunbeam.ListJujuUsers(s)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, users)
}

func cmdJujuUsersGet(s *state.State, r *http.Request) response.Response {
	var name string
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.InternalError(err)
	}
	jujuUser, err := sunbeam.GetJujuUser(s, name)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, jujuUser)
}

func cmdJujuUsersPost(s *state.State, r *http.Request) response.Response {
	var req types.JujuUser

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.AddJujuUser(s, req.Username, req.Token)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdJujuUsersDelete(s *state.State, r *http.Request) response.Response {
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.SmartError(err)
	}
	err = sunbeam.DeleteJujuUser(s, name)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
