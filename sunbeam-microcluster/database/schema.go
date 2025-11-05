// Package database provides the database access functions and schema.
package database

import (
	"context"
	"database/sql"

	"github.com/canonical/lxd/lxd/db/schema"
)

// SchemaExtensions is a list of schema extensions that can be passed to the MicroCluster daemon.
// Each entry will increase the database schema version by one, and will be applied after internal schema updates.
var SchemaExtensions = []schema.Update{
	NodesSchemaUpdate,
	ConfigSchemaUpdate,
	JujuUserSchemaUpdate,
	ManifestsSchemaUpdate,
	AddSystemIDToNodes,
	StorageBackendSchemaUpdate,
}

// NodesSchemaUpdate is schema for table nodes
func NodesSchemaUpdate(_ context.Context, tx *sql.Tx) error {
	stmt := `
CREATE TABLE nodes (
  id                            INTEGER  PRIMARY KEY AUTOINCREMENT NOT NULL,
  member_id                     INTEGER  NOT  NULL,
  name                          TEXT     NOT  NULL,
  role                          TEXT,
  machine_id                    INTEGER,
  FOREIGN KEY (member_id) REFERENCES "core_cluster_members" (id)
  UNIQUE(name)
);
  `

	_, err := tx.Exec(stmt)

	return err
}

// ConfigSchemaUpdate is schema for table config
func ConfigSchemaUpdate(_ context.Context, tx *sql.Tx) error {
	stmt := `
CREATE TABLE config (
  id                            INTEGER  PRIMARY KEY AUTOINCREMENT NOT NULL,
  key                           TEXT     NOT  NULL,
  value                         TEXT     NOT  NULL,
  UNIQUE(key)
);
  `

	_, err := tx.Exec(stmt)

	return err
}

// JujuUserSchemaUpdate is schema for table jujuuser
func JujuUserSchemaUpdate(_ context.Context, tx *sql.Tx) error {
	stmt := `
CREATE TABLE jujuuser (
  id                            INTEGER  PRIMARY KEY AUTOINCREMENT NOT NULL,
  username                      TEXT     NOT  NULL,
  token                         TEXT     NOT  NULL,
  UNIQUE(username)
);
  `

	_, err := tx.Exec(stmt)

	return err
}

// ManifestsSchemaUpdate is schema for table manifest
// TOCHK: TIMESTAMP(6) not storing nano seconds
func ManifestsSchemaUpdate(_ context.Context, tx *sql.Tx) error {
	stmt := `
CREATE TABLE manifest (
  id                            INTEGER  PRIMARY KEY AUTOINCREMENT NOT NULL,
  manifest_id                   TEXT     NOT  NULL,
  applied_date                  TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP,
  data                          TEXT,
  UNIQUE(manifest_id)
);
  `
	_, err := tx.Exec(stmt)

	return err
}

// AddSystemIDToNodes is schema update for table nodes
func AddSystemIDToNodes(_ context.Context, tx *sql.Tx) error {
	stmt := `
ALTER TABLE nodes ADD COLUMN system_id TEXT default '';
  `

	_, err := tx.Exec(stmt)

	return err
}

// StorageBackendSchemaUpdate is schema for table storage_backends
func StorageBackendSchemaUpdate(_ context.Context, tx *sql.Tx) error {
	stmt := `
CREATE TABLE storage_backends (
  id                            INTEGER  PRIMARY KEY AUTOINCREMENT NOT NULL,
  name                          TEXT     NOT NULL,
  type                          TEXT     NOT NULL,
  principal                     TEXT,
  model_uuid                    TEXT,
  config                        TEXT,
  UNIQUE(name)
);
  `

	_, err := tx.Exec(stmt)
	return err
}
