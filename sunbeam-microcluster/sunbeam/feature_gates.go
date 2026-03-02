package sunbeam

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
)

// ListFeatureGates returns all the feature gates.
func ListFeatureGates(ctx context.Context, s state.State) (apitypes.FeatureGates, error) {
	gates := apitypes.FeatureGates{}

	// Get the feature gates from the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		records, err := database.GetFeatureGates(ctx, tx)
		if err != nil {
			return fmt.Errorf("Failed to fetch feature gates: %w", err)
		}

		for _, gate := range records {
			gates = append(gates, apitypes.FeatureGate{
				GateKey: gate.GateKey,
				Enabled: gate.Enabled,
			})
		}

		return nil
	})
	if err != nil {
		return nil, err
	}

	return gates, nil
}

// GetFeatureGate returns a FeatureGate with the given gate key.
func GetFeatureGate(ctx context.Context, s state.State, gateKey string) (apitypes.FeatureGate, error) {
	gate := apitypes.FeatureGate{}
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		record, err := database.GetFeatureGate(ctx, tx, gateKey)
		if err != nil {
			return err
		}

		gate.GateKey = record.GateKey
		gate.Enabled = record.Enabled

		return nil
	})
	if err != nil {
		return apitypes.FeatureGate{}, err
	}
	return gate, nil
}

// AddFeatureGate adds a feature gate to the database.
func AddFeatureGate(ctx context.Context, s state.State, gateKey string, enabled bool) error {
	// Add feature gate to the database.
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		_, err := database.CreateFeatureGate(ctx, tx, database.FeatureGate{
			GateKey: gateKey,
			Enabled: enabled,
		})
		if err != nil {
			return fmt.Errorf("Failed to record feature gate: %w", err)
		}

		return nil
	})
}

// UpdateFeatureGate updates a feature gate record in the database.
func UpdateFeatureGate(ctx context.Context, s state.State, gateKey string, enabled bool) error {
	// Update feature gate in the database.
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		_, err := database.GetFeatureGate(ctx, tx, gateKey)
		if err != nil {
			return fmt.Errorf("Failed to retrieve feature gate details: %w", err)
		}

		return database.UpdateFeatureGate(ctx, tx, gateKey, database.FeatureGate{
			GateKey: gateKey,
			Enabled: enabled,
		})
	})

	if err != nil {
		return fmt.Errorf("Failed to update feature gate %s: %w", gateKey, err)
	}

	return nil
}

// DeleteFeatureGate deletes a feature gate from the database.
func DeleteFeatureGate(ctx context.Context, s state.State, gateKey string) error {
	// Delete feature gate from the database.
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		err := database.DeleteFeatureGate(ctx, tx, gateKey)
		if err != nil {
			return fmt.Errorf("Failed to delete feature gate: %w", err)
		}

		return nil
	})
}
