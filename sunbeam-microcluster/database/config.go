package database

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/canonical/lxd/lxd/db/query"
)

//go:generate -command mapper lxd-generate db mapper -t config.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e ConfigItem objects table=config
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e ConfigItem objects-by-Key table=config
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e ConfigItem id table=config
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e ConfigItem create table=config
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e ConfigItem delete-by-Key table=config
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e ConfigItem update table=config

//
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem GetMany table=config
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem GetOne table=config
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem ID table=config
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem Exists table=config
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem Create table=config
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem DeleteOne-by-Key table=config
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e ConfigItem Update table=config

// ConfigItem is used to track the Ceph configuration.
type ConfigItem struct {
	ID    int
	Key   string `db:"primary=yes"`
	Value string
}

// ConfigItemFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type ConfigItemFilter struct {
	Key *string
}

// GetConfigItemKeys returns the list of ConfigItem keys from the database, filtered by prefix if provided.
func GetConfigItemKeys(ctx context.Context, tx *sql.Tx, prefix *string) ([]string, error) {
	stmt := `SELECT config.key FROM config`

	args := make([]any, 0)

	if prefix != nil {
		stmt += ` WHERE config.key LIKE ?`
		args = append(args, *prefix+"%")
	}

	configs := make([]string, 0)

	dest := func(scan func(dest ...any) error) error {
		var key string
		err := scan(&key)
		if err != nil {
			return err
		}

		configs = append(configs, key)

		return nil
	}

	err := query.Scan(ctx, tx, stmt, dest, args...)
	if err != nil {
		return nil, fmt.Errorf("Failed to fetch from \"config\" table: %w", err)
	}

	return configs, nil
}
