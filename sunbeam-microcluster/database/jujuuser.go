package database

//go:generate -command mapper lxd-generate db mapper -t jujuuser.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser objects table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser objects-by-Username table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser id table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser create table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser delete-by-Username table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser update table=jujuuser
//
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser GetMany table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser GetOne table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser ID table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser Exists table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser Create table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser DeleteOne-by-Username table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e JujuUser Update table=jujuuser

// JujuUser is used to track User and registration token information.
type JujuUser struct {
	ID       int
	Username string `db:"primary=yes"`
	Token    string
}

// JujuUserFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type JujuUserFilter struct {
	Username *string
}
