package database

import (
	"context"
	"database/sql"
	"fmt"
	"strings"

	"github.com/canonical/microcluster/v2/cluster"
)

//go:generate -command mapper lxd-generate db mapper -t node.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node objects table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node objects-by-Member table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node objects-by-Name table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node objects-by-Role table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node objects-by-MachineID table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node id table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node create table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node delete-by-Name table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e node update table=nodes
//
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node GetMany
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node GetOne
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node ID
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node Exists
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node Create
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node DeleteOne-by-Name
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e node Update

// Node is used to track Node information.
type Node struct {
	ID        int
	Member    string `db:"join=core_cluster_members.name&joinon=nodes.member_id"`
	Name      string `db:"primary=yes"`
	Role      string
	MachineID int
	SystemID  string
	Arch      string
	IsDPU     bool
	ImageName  string
}

// NodeFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type NodeFilter struct {
	Member    *string
	Name      *string
	Role      *string
	MachineID *int
}

// GetNodesFromRoles returns a slice of Nodes that match the given roles.
func GetNodesFromRoles(ctx context.Context, tx *sql.Tx, roles []string) ([]Node, error) {

	stmt, err := cluster.StmtString(nodeObjects)

	if err != nil {
		return nil, fmt.Errorf("Failed to fetch prepared statement nodeObjets: %v", err)
	}

	queryParts := strings.SplitN(stmt, "ORDER BY", 2)

	args := make([]any, 0)

	if len(roles) > 0 {
		queryParts[0] += " WHERE"
		for i, role := range roles {
			if i > 0 {
				queryParts[0] += " AND"
			}
			queryParts[0] += " instr(nodes.role, ?) > 0"
			// Role is a json list, quote it to cheaply do full string match
			args = append(args, "\""+role+"\"")
		}
	}

	stmt = strings.Join(queryParts, " ORDER BY")

	nodes, err := getNodesRaw(ctx, tx, stmt, args...)
	if err != nil {
		return nil, fmt.Errorf("Failed to fetch from \"nodes\" table: %w", err)
	}

	return nodes, nil

}
