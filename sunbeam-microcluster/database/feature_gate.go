package database

//go:generate -command mapper lxd-generate db mapper -t feature_gate.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate objects table=feature_gates
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate objects-by-GateKey table=feature_gates
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate id table=feature_gates
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate create table=feature_gates
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate delete-by-GateKey table=feature_gates
//go:generate mapper stmt -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate update table=feature_gates
//
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate GetMany
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate GetOne
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate ID
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate Exists
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate Create
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate DeleteOne-by-GateKey
//go:generate mapper method -i -d github.com/canonical/microcluster/v3/microcluster/db -e FeatureGate Update

// FeatureGate is used to track feature gate configuration.
type FeatureGate struct {
	ID      int
	GateKey string `db:"primary=yes"`
	Enabled bool
}

// FeatureGateFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type FeatureGateFilter struct {
	GateKey *string
}
