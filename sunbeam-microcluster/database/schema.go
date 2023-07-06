// Package database provides the database access functions and schema.
package database

import (
	"context"
	"database/sql"

	"github.com/canonical/lxd/lxd/db/schema"
)

// SchemaExtensions is a list of schema extensions that can be passed to the MicroCluster daemon.
// Each entry will increase the database schema version by one, and will be applied after internal schema updates.
var SchemaExtensions = map[int]schema.Update{
	1: NodesSchemaUpdate,
	2: ConfigSchemaUpdate,
	3: JujuUserSchemaUpdate,
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
  FOREIGN KEY (member_id) REFERENCES "internal_cluster_members" (id)
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
