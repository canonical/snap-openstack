package sunbeam

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
)

// ListStorageBackends return all the storage backends, filterable by role (Optional)
func ListStorageBackends(ctx context.Context, s state.State) (apitypes.StorageBackends, error) {
	backends := apitypes.StorageBackends{}

	// Get the storage backends from the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		records, err := database.GetStorageBackends(ctx, tx)
		if err != nil {
			return fmt.Errorf("Failed to fetch storage backends: %w", err)
		}

		for _, backend := range records {

			backends = append(backends, apitypes.StorageBackend{
				Name:      backend.Name,
				Type:      backend.Type,
				Principal: backend.Principal,
				ModelUUID: backend.ModelUUID,
				Config:    backend.Config,
			})
		}

		return nil
	})
	if err != nil {
		return nil, err
	}

	return backends, nil

}

// GetStorageBackend returns a StorageBackend with the given name
func GetStorageBackend(ctx context.Context, s state.State, name string) (apitypes.StorageBackend, error) {
	backend := apitypes.StorageBackend{}
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		record, err := database.GetStorageBackend(ctx, tx, name)
		if err != nil {
			return err
		}

		backend.Name = record.Name
		backend.Type = record.Type
		backend.Principal = record.Principal
		backend.ModelUUID = record.ModelUUID
		backend.Config = record.Config

		return nil
	})
	if err != nil {
		return apitypes.StorageBackend{}, err
	}
	return backend, nil
}

// AddStorageBackend adds a storage backend to the database
func AddStorageBackend(ctx context.Context, s state.State, name string, backendType string, principal string, modelUUID string, config string) error {
	// Add storage backend to the database.
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		_, err := database.CreateStorageBackend(ctx, tx, database.StorageBackend{
			Name:      name,
			Type:      backendType,
			Principal: principal,
			ModelUUID: modelUUID,
			Config:    config,
		})
		if err != nil {
			return fmt.Errorf("Failed to record storage backend: %w", err)
		}

		return nil
	})
}

// UpdateStorageBackend updates a storage backend record in the database
func UpdateStorageBackend(ctx context.Context, s state.State, name string, backendType string, principal string, modelUUID string, config string) error {
	// Update storage backend to the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		backend, err := database.GetStorageBackend(ctx, tx, name)
		if err != nil {
			return fmt.Errorf("Failed to retrieve storage backend details: %w", err)
		}

		if backendType == "" {
			backendType = backend.Type
		}
		if principal == "" {
			principal = backend.Principal
		}
		if modelUUID == "" {
			modelUUID = backend.ModelUUID
		}
		if config == "" {
			config = backend.Config
		}

		err = database.UpdateStorageBackend(ctx, tx, name, database.StorageBackend{Name: name, Type: backendType, Principal: principal, ModelUUID: modelUUID, Config: config})
		if err != nil {
			return fmt.Errorf("Failed to update record storage backend: %w", err)
		}

		return nil
	})

	return err
}

// DeleteStorageBackend deletes a storage backend from database
func DeleteStorageBackend(ctx context.Context, s state.State, name string) error {
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		return database.DeleteStorageBackend(ctx, tx, name)
	})

}
