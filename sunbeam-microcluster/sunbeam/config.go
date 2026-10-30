// Package sunbeam provides the interface to talk to database
package sunbeam

import (
	"context"
	"database/sql"
	"fmt"
	"strings"

	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
)

// GetConfig returns the ConfigItem based on key from the database
func GetConfig(ctx context.Context, s state.State, key string) (string, error) {
	var value string

	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		record, err := database.GetConfigItem(ctx, tx, key)
		if err != nil {
			return err
		}
		value = record.Value
		return nil
	})

	if err != nil {
		return "", err
	}

	return value, nil
}

// GetConfigItemKeys returns the list of ConfigItem keys from the database
func GetConfigItemKeys(ctx context.Context, s state.State, prefix *string) ([]string, error) {
	var keys []string

	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		keys, err = database.GetConfigItemKeys(ctx, tx, prefix)
		if err != nil {
			return err
		}
		return nil
	})

	if err != nil {
		return nil, err
	}

	return keys, nil
}

// CreateConfig adds a new ConfigItem to the database
func CreateConfig(ctx context.Context, s state.State, key string, value string) error {

	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		_, err := database.CreateConfigItem(ctx, tx, database.ConfigItem{Key: key, Value: value})
		if err != nil {
			return fmt.Errorf("failed to record config item: %w", err)
		}
		return nil
	})
}

// UpdateConfig updates a ConfigItem in the database
func UpdateConfig(ctx context.Context, s state.State, key string, value string) error {
	configItem := database.ConfigItem{Key: key, Value: value}

	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		err := database.UpdateConfigItem(ctx, tx, key, configItem)
		if err != nil && strings.Contains(err.Error(), "ConfigItem not found") {
			_, err = database.CreateConfigItem(ctx, tx, configItem)
		}
		if err != nil {
			return fmt.Errorf("failed to record config item: %w", err)
		}

		return nil
	})
}

// DeleteConfig deletes a ConfigItem from the database
func DeleteConfig(ctx context.Context, s state.State, key string) error {
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		return database.DeleteConfigItem(ctx, tx, key)
	})
}
