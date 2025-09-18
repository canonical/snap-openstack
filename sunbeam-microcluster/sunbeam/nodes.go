package sunbeam

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"sort"

	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
)

// ListNodes return all the nodes, filterable by role (Optional)
func ListNodes(ctx context.Context, s state.State, roles []string) (apitypes.Nodes, error) {
	nodes := apitypes.Nodes{}

	// Get the nodes from the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		records, err := database.GetNodesFromRoles(ctx, tx, roles)
		if err != nil {
			return fmt.Errorf("Failed to fetch nodes: %w", err)
		}

		for _, node := range records {
			nodeRole, err := roleFromStr(node.Role)
			if err != nil {
				return err
			}
			nodes = append(nodes, apitypes.Node{
				Name:      node.Name,
				Role:      nodeRole,
				MachineID: node.MachineID,
				SystemID:  node.SystemID,
			})
		}

		return nil
	})
	if err != nil {
		return nil, err
	}

	return nodes, nil
}

// GetNode returns a Node with the given name
func GetNode(ctx context.Context, s state.State, name string) (apitypes.Node, error) {
	node := apitypes.Node{MachineID: -1}
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		record, err := database.GetNode(ctx, tx, name)
		if err != nil {
			return err
		}

		nodeRole, err := roleFromStr(record.Role)
		if err != nil {
			return err
		}
		node.Name = record.Name
		node.Role = nodeRole
		node.MachineID = record.MachineID
		node.SystemID = record.SystemID

		return nil
	})

	return node, err
}

// AddNode adds a node to the database
func AddNode(ctx context.Context, s state.State, name string, role []string, machineid int, systemid string) error {
	nodeRole, err := roleToStr(role)
	if err != nil {
		return err
	}
	// Add node to the database.
	err = s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		_, err := database.CreateNode(ctx, tx, database.Node{Member: s.Name(), Name: name, Role: nodeRole, MachineID: machineid, SystemID: systemid})
		if err != nil {
			return fmt.Errorf("Failed to record node: %w", err)
		}

		return nil
	})
	if err != nil {
		return err
	}

	return nil
}

// UpdateNode updates a node record in the database
func UpdateNode(ctx context.Context, s state.State, name string, role []string, machineid int, systemid string) error {
	nodeRole, err := roleToStr(role)
	if err != nil {
		return err
	}
	// Update node to the database.
	err = s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		node, err := database.GetNode(ctx, tx, name)
		if err != nil {
			return fmt.Errorf("Failed to retrieve node details: %w", err)
		}

		if role == nil {
			nodeRole = node.Role
		}
		if machineid == -1 {
			machineid = node.MachineID
		}
		if systemid == "" {
			systemid = node.SystemID
		}

		err = database.UpdateNode(ctx, tx, name, database.Node{Member: s.Name(), Name: name, Role: nodeRole, MachineID: machineid, SystemID: systemid})
		if err != nil {
			return fmt.Errorf("Failed to update record node: %w", err)
		}

		return nil
	})
	if err != nil {
		return err
	}

	return nil
}

// DeleteNode deletes a node from database
func DeleteNode(ctx context.Context, s state.State, name string) error {
	// Delete node from the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		err := database.DeleteNode(ctx, tx, name)
		if err != nil {
			return fmt.Errorf("Failed to delete node: %w", err)
		}

		return nil
	})
	if err != nil {
		return err
	}

	return nil
}

// roleToStr converts a role slice to a string sorted
func roleToStr(role []string) (string, error) {
	sort.Strings(role)
	roleJSON, err := json.Marshal(role)
	if err != nil {
		return "", fmt.Errorf("Failed to marshal role: %w", err)
	}
	return string(roleJSON), nil
}

// roleFromStr converts a role string to a slice sorted
func roleFromStr(roleStr string) ([]string, error) {
	var role []string
	err := json.Unmarshal([]byte(roleStr), &role)
	if err != nil {
		return nil, fmt.Errorf("Failed to unmarshal role: %w", err)
	}
	sort.Strings(role)
	return role, nil
}
