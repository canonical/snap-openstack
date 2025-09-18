package sunbeam

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
)

// ListManifests return all the manifests
func ListManifests(ctx context.Context, s state.State) (apitypes.Manifests, error) {
	manifests := apitypes.Manifests{}

	// Get the manifests from the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		records, err := database.GetManifestItems(ctx, tx)
		if err != nil {
			return fmt.Errorf("Failed to fetch manifests: %w", err)
		}

		for _, manifest := range records {
			manifests = append(manifests, apitypes.Manifest{
				ManifestID:  manifest.ManifestID,
				AppliedDate: manifest.AppliedDate,
				Data:        manifest.Data,
			})
		}

		return nil
	})
	if err != nil {
		return nil, err
	}

	return manifests, nil
}

// GetManifest returns a Manifest with the given id
func GetManifest(ctx context.Context, s state.State, manifestid string) (apitypes.Manifest, error) {
	manifest := apitypes.Manifest{}

	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		var record *database.ManifestItem
		var err error
		// If manifest id is latest, retrieve the latest inserted record.
		if manifestid == "latest" {
			record, err = database.GetLatestManifestItem(ctx, tx)
		} else {
			record, err = database.GetManifestItem(ctx, tx, manifestid)
		}
		if err != nil {
			return err
		}

		manifest.ManifestID = record.ManifestID
		manifest.AppliedDate = record.AppliedDate
		manifest.Data = record.Data

		return nil
	})

	return manifest, err
}

// AddManifest adds a manifest to the database
func AddManifest(ctx context.Context, s state.State, manifestid string, data string) error {
	// Add manifest to the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		_, err := database.CreateManifestItem(ctx, tx, database.ManifestItem{ManifestID: manifestid, Data: data})
		if err != nil {
			return fmt.Errorf("Failed to record manifest: %w", err)
		}

		return nil
	})
	if err != nil {
		return err
	}

	return nil
}

// DeleteManifest deletes a manifest from database
func DeleteManifest(ctx context.Context, s state.State, manifestid string) error {
	// Delete manifest from the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		err := database.DeleteManifestItem(ctx, tx, manifestid)
		if err != nil {
			return fmt.Errorf("Failed to delete manifest: %w", err)
		}

		return nil
	})
	if err != nil {
		return err
	}

	return nil
}
