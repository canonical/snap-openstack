package database

import (
	"context"
	"database/sql"
	"fmt"
	"net/http"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v2/cluster"
)

//go:generate -command mapper lxd-generate db mapper -t manifest.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e ManifestItem objects table=manifest
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e ManifestItem objects-by-ManifestID table=manifest
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e ManifestItem id table=manifest
//go:generate mapper stmt -d github.com/canonical/microcluster/v2/cluster -e ManifestItem delete-by-ManifestID table=manifest

//
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e ManifestItem GetMany table=manifest
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e ManifestItem GetOne table=manifest
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e ManifestItem ID table=manifest
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e ManifestItem Exists table=manifest
//go:generate mapper method -i -d github.com/canonical/microcluster/v2/cluster -e ManifestItem DeleteOne-by-ManifestID table=manifest

// ManifestItem is used to save the Sunbeam manifests provided by user.
// AppliedDate is saved as Timestamp in database but retreived as string
// Probable Bug: https://github.com/mattn/go-sqlite3/issues/951
type ManifestItem struct {
	ID          int
	ManifestID  string `db:"primary=yes"`
	AppliedDate string
	Data        string
}

// ManifestItemFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type ManifestItemFilter struct {
	ManifestID *string
}

var manifestItemCreate = cluster.RegisterStmt(`
INSERT INTO manifest (manifest_id, data)
  VALUES (?, ?)
`)

var latestManifestItemObject = cluster.RegisterStmt(`
SELECT manifest.id, manifest.manifest_id, manifest.applied_date, manifest.data
  FROM manifest
  WHERE manifest.applied_date = (SELECT MAX(applied_date) FROM manifest)
`)

// CreateManifestItem adds a new ManifestItem to the database.
// generator: ManifestItem Create
func CreateManifestItem(ctx context.Context, tx *sql.Tx, object ManifestItem) (int64, error) {
	// Check if a ManifestItem with the same key exists.
	exists, err := ManifestItemExists(ctx, tx, object.ManifestID)
	if err != nil {
		return -1, fmt.Errorf("failed to check for duplicates: %w", err)
	}

	if exists {
		return -1, api.StatusErrorf(http.StatusConflict, "This \"manifest\" entry already exists")
	}

	args := make([]any, 2)

	// Populate the statement arguments.
	args[0] = object.ManifestID
	args[1] = object.Data

	// Prepared statement to use.
	stmt, err := cluster.Stmt(tx, manifestItemCreate)
	if err != nil {
		return -1, fmt.Errorf("failed to get \"manifestItemCreate\" prepared statement: %w", err)
	}

	// Execute the statement.
	result, err := stmt.Exec(args...)
	if err != nil {
		return -1, fmt.Errorf("failed to create \"manifest\" entry: %w", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return -1, fmt.Errorf("failed to fetch \"manifest\" entry ID: %w", err)
	}

	return id, nil
}

// GetLatestManifestItem returns the latest inserted record in manifest table.
func GetLatestManifestItem(ctx context.Context, tx *sql.Tx) (*ManifestItem, error) {
	var err error

	// Pick the prepared statement and arguments to use based on active criteria.
	var sqlStmt *sql.Stmt

	sqlStmt, err = cluster.Stmt(tx, latestManifestItemObject)
	if err != nil {
		return nil, fmt.Errorf("failed to get \"manifestItemObjects\" prepared statement: %w", err)
	}

	// Result slice.
	// objects := make([]ManifestItem, 0)
	objects, err := getManifestItems(ctx, sqlStmt)
	if err != nil {
		return nil, fmt.Errorf("failed to fetch from \"manifest\" table: %w", err)
	}

	objectsLen := len(objects)
	switch objectsLen {
	case 0:
		return nil, api.StatusErrorf(http.StatusNotFound, "ManifestItem not found")
	default:
		return &objects[objectsLen-1], nil
	}
}
