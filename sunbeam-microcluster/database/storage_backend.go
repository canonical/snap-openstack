package database

//go:generate -command mapper lxd-generate db mapper -t storage_backend.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend objects table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend objects-by-Name table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend objects-by-Type table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend objects-by-Principal table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend objects-by-ModelUUID table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend id table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend create table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend delete-by-Name table=storage_backends
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend update table=storage_backends
//
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend GetMany
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend GetOne
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend ID
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend Exists
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend Create
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend DeleteOne-by-Name
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e StorageBackend Update

// StorageBackend is used to track StorageBackend information.
type StorageBackend struct {
	ID        int
	Name      string `db:"primary=yes"`
	Type      string
	Config    string
	Principal string
	ModelUUID string
}

// StorageBackendFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type StorageBackendFilter struct {
	Name      *string
	Type      *string
	Principal *string
	ModelUUID *string
}
