# Cilium Per-Host Device Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragile cluster-wide cilium device patterns with surgical per-host `CiliumNodeConfig` resources that name the exact internal-space NIC on each control node.

**Architecture:** Revert three commits that introduced the cluster-wide annotation approach, extract shared per-host logic from `EnsureL2AdvertisementByHostStep` into `_PerHostK8SResourceStep`, then build `EnsureCiliumDeviceByHostStep` on top of it. Integrate into all command plans before the L2Advertisement steps.

**Tech Stack:** Python 3.12, lightkube (k8s client), jubilant (juju), pytest, Cilium `cilium.io/v2` CRD

**Spec:** `docs/agents/specs/cilium-per-host-device-config-design.md`

---

### Task 1: Revert the three commits

**Files:**
- Modify: `sunbeam-python/sunbeam/steps/k8s.py`
- Modify: `sunbeam-python/tests/unit/sunbeam/steps/test_k8s.py`
- Modify: `sunbeam-python/tests/unit/sunbeam/provider/maas/test_maas.py`
- Modify: `sunbeam-python/sunbeam/steps/upgrades/intra_channel.py`

- [ ] **Step 1: Revert newest commit first**

```bash
git revert --no-commit f001990cf7e3c81a0c040224016ebc66c5290b5d
```

- [ ] **Step 2: Revert second commit**

```bash
git revert --no-commit 2be63d7bf63c46e7ab77bd45bcd519fec9275a06
```

- [ ] **Step 3: Revert oldest commit**

```bash
git revert --no-commit 4627372886174154be4784761f04de77881c5aae
```

- [ ] **Step 4: Resolve any conflicts and verify the revert**

After the three reverts, verify these are gone from `sunbeam-python/sunbeam/steps/k8s.py`:
- `CILIUM_DEVICES_ANNOTATION_KEY` constant (was line 106)
- `CILIUM_DEVICES_ANNOTATION_DEFAULT` constant (was lines 107-121)
- `EnsureCiliumOnCorrectSpaceStep` class (was lines 589-794)
- The `"cluster"` endpoint binding to `Networks.INTERNAL` in `DeployK8SApplicationStep.extra_tfvars()` — should only have the management default binding
- The `cluster-annotations` line in `_get_k8s_config_tfvars()` and the comma-separated validation block
- `_get_cluster_ips` should be reverted to `_get_management_ips` in `EnsureK8SUnitsTaggedStep`

Verify `intra_channel.py` no longer imports `EnsureCiliumOnCorrectSpaceStep`.

- [ ] **Step 5: Run tests to confirm nothing is broken**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py -v 2>&1 | tail -30
```

Expected: all tests pass (the reverted tests are removed too).

- [ ] **Step 6: Run linting**

```bash
cd sunbeam-python && tox -e pep8 2>&1 | tail -20
```

Expected: clean pass.

- [ ] **Step 7: Commit the revert**

```bash
git add -A && git commit -m "revert: remove cluster-wide cilium device annotation approach

Reverts f001990c, 2be63d7b, 46273728.

The cluster-wide device pattern matching causes Cilium to bind to
wrong devices (e.g. OVS uplink NICs), breaking external traffic.
A per-host CiliumNodeConfig approach will replace this."
```

---

### Task 2: Add `CiliumNodeConfig` generic resource to `K8SHelper`

**Files:**
- Modify: `sunbeam-python/sunbeam/core/k8s.py:122-132`

- [ ] **Step 1: Add the class method**

In `sunbeam-python/sunbeam/core/k8s.py`, add after `get_lightkube_l2_advertisement_resource` (around line 132):

```python
    @classmethod
    def get_lightkube_cilium_node_config_resource(
        cls,
    ) -> Type["l_generic_resource.GenericNamespacedResource"]:
        """Return lightkube generic resource of type CiliumNodeConfig."""
        return l_generic_resource.create_namespaced_resource(
            "cilium.io",
            "v2",
            "CiliumNodeConfig",
            "ciliumnodeconfigs",
            verbs=["delete", "get", "list", "patch", "post", "put"],
        )
```

- [ ] **Step 2: Run linting**

```bash
cd sunbeam-python && tox -e pep8 2>&1 | tail -20
```

Expected: clean pass.

- [ ] **Step 3: Commit**

```bash
git add sunbeam-python/sunbeam/core/k8s.py && git commit -m "feat: add CiliumNodeConfig lightkube generic resource to K8SHelper"
```

---

### Task 3: Extract `_PerHostK8SResourceStep` base class

**Files:**
- Modify: `sunbeam-python/sunbeam/steps/k8s.py:1506-1690` (EnsureL2AdvertisementByHostStep)
- Test: `sunbeam-python/tests/unit/sunbeam/steps/test_k8s.py`

This task extracts the shared logic and refactors `EnsureL2AdvertisementByHostStep` to inherit from it. The L2 tests must keep passing with zero behavior change.

- [ ] **Step 1: Run existing L2 tests to establish baseline**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py::TestEnsureL2AdvertisementByHostStep -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 2: Write `_PerHostK8SResourceStep` above `EnsureL2AdvertisementByHostStep`**

Insert before the `EnsureL2AdvertisementByHostStep` class in `sunbeam-python/sunbeam/steps/k8s.py`:

```python
class _PerHostK8SResourceStep(BaseStep):
    """Base class for steps that manage per-host k8s resources.

    Provides common logic for looking up control nodes, creating a kube client,
    finding the juju-space interface for each node, and determining which nodes
    have outdated resources.

    Subclasses must implement:
      - _get_outdated_resources(nodes, kube) -> (outdated, deleted)
      - run(context) -> Result
    """

    class _InterfaceError(SunbeamException):
        pass

    def __init__(
        self,
        name: str,
        description: str,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        network: Networks,
        kube_namespace: str | None = None,
        fqdn: str | None = None,
    ):
        super().__init__(name, description)
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.network = network
        self.kube_namespace = kube_namespace
        self.fqdn = fqdn
        self.to_update: list[dict] = []
        self.to_delete: list[dict] = []
        self._ifnames: dict[str, str] = {}

    def _get_interface(self, node: dict) -> str:
        """Get the network interface for the node in the configured space."""
        name = node["name"]
        if name in self._ifnames:
            return self._ifnames[name]
        machine_id = str(node["machineid"])
        machine_interfaces = self.jhelper.get_machine_interfaces(
            self.model, machine_id
        )
        LOG.debug("Machine %r interfaces: %r", machine_id, machine_interfaces)
        network_space = self.deployment.get_space(self.network)
        for ifname, iface in machine_interfaces.items():
            if (spaces := iface.space) and network_space in spaces.split():
                self._ifnames[name] = ifname
                return ifname
        raise self._InterfaceError(
            f"Node {node['name']} has no interface in {self.network.name} space"
        )

    def _get_outdated_resources(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        """Return (outdated, deleted) node name lists.

        Must be implemented by subclasses.
        """
        raise NotImplementedError

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not."""
        control = Role.CONTROL.name.lower()
        region_controller = Role.REGION_CONTROLLER.name.lower()
        if self.fqdn:
            node = self.client.cluster.get_node_info(self.fqdn)
            node_roles = node.get("role", [])
            if control not in node_roles and region_controller not in node_roles:
                return Result(ResultType.FAILED, f"{self.fqdn} is not a control node")
            self.control_nodes = [node]
        else:
            self.control_nodes = self.client.cluster.list_nodes_by_role(control)

        try:
            self.kube = get_kube_client(self.client, self.kube_namespace)
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            outdated, deleted = self._get_outdated_resources(
                self.control_nodes, self.kube
            )
        except (l_exceptions.ApiError, self._InterfaceError) as e:
            LOG.debug("Failed to get outdated resources", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        if not (outdated or deleted):
            LOG.debug("No resources to update")
            return Result(ResultType.SKIPPED)

        for node in self.control_nodes:
            if node["name"] in outdated:
                self.to_update.append(node)
            if node["name"] in deleted:
                self.to_delete.append(node)

        return Result(ResultType.COMPLETED)
```

- [ ] **Step 3: Refactor `EnsureL2AdvertisementByHostStep` to inherit from `_PerHostK8SResourceStep`**

Replace the class definition. Remove the duplicated `__init__`, `_get_interface`, and `is_skip` — those now live in the base. Keep L2-specific fields and methods:

```python
class EnsureL2AdvertisementByHostStep(_PerHostK8SResourceStep):
    """Ensure IP Pool is advertised by L2Advertisement resources."""

    _APPLICATION = APPLICATION

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        network: Networks,
        pool: str,
        fqdn: str | None = None,
    ):
        super().__init__(
            "Ensure L2 advertisement",
            "Ensuring L2 advertisement",
            deployment,
            client,
            jhelper,
            model,
            network,
            kube_namespace=K8SHelper.get_loadbalancer_namespace(),
            fqdn=fqdn,
        )
        self.pool = pool
        self.l2_advertisement_resource = (
            K8SHelper.get_lightkube_l2_advertisement_resource()
        )
        self.l2_advertisement_namespace = K8SHelper.get_loadbalancer_namespace()

    def _labels(self, name: str, space: str) -> dict[str, str]:
        """Return labels for the L2 advertisement."""
        return {
            "app.kubernetes.io/managed-by": self.deployment.name,
            "app.kubernetes.io/instance": self._instance_label(
                self.network.value.lower(), name
            ),
            "app.kubernetes.io/name": self._name_label(self.network.value.lower()),
            HOSTNAME_LABEL: name,
            "sunbeam/space": space,
            "sunbeam/network": self.network.value.lower(),
        }

    def _l2_advertisement_name(self, node: str) -> str:
        """Return L2 advertisement name for the node."""
        return f"{self.network.value.lower()}-{node}"

    def _name_label(self, network: str):
        """Return name label for the L2 advertisement."""
        return f"{network}-l2"

    def _instance_label(self, network: str, name: str):
        """Return instance label for the L2 advertisement."""
        return self._name_label(network) + "-" + name

    def _get_outdated_resources(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        """Get outdated L2 advertisement."""
        outdated: list[str] = [node["name"] for node in nodes]
        deleted: list[str] = []

        l2_advertisements = kube.list(
            self.l2_advertisement_resource,
            namespace=self.l2_advertisement_namespace,
            labels={"app.kubernetes.io/name": self._name_label(self.pool)},
        )

        for l2_ad in l2_advertisements:
            if l2_ad.metadata is None or l2_ad.metadata.labels is None:
                LOG.debug("L2 advertisement has no metadata nor labels")
                continue
            hostname = l2_ad.metadata.labels.get(HOSTNAME_LABEL)

            if hostname is None:
                LOG.debug(
                    "L2 advertisement %s has no hostname label",
                    l2_ad.metadata.name,
                )
                continue
            if l2_ad.spec is None:
                LOG.debug("L2 advertisement %r has no spec", hostname)
                continue
            if hostname not in outdated:
                LOG.debug(
                    "L2 advertisement %s has no matching node",
                    l2_ad.metadata.name,
                )
                deleted.append(hostname)
                continue
            if self.pool not in l2_ad.spec.get("ipAddressPools", []):
                LOG.debug(
                    "L2 advertisement %s has wrong allocated ip pool",
                    l2_ad.metadata.name,
                )
                continue
            interface = None
            for node in nodes:
                if node["name"] == hostname:
                    interface = self._get_interface(node)
            if not interface:
                LOG.debug(
                    "L2 advertisement %s has no allocated interface",
                    l2_ad.metadata.name,
                )
                continue
            if l2_ad.spec.get("interfaces") != [interface]:
                LOG.debug(
                    "L2 advertisement %s has wrong allocated interface",
                    l2_ad.metadata.name,
                )
                continue
            outdated.remove(hostname)
        return outdated, deleted

    @tenacity.retry(
        wait=tenacity.wait_fixed(15),
        stop=tenacity.stop_after_delay(600),
        retry=tenacity.retry_if_exception_type(tenacity.TryAgain),
        reraise=True,
    )
    def _ensure_l2_advertisement(self, name: str, interface: str):
        try:
            self.kube.apply(
                self.l2_advertisement_resource(
                    metadata=meta_v1.ObjectMeta(
                        name=self._l2_advertisement_name(name),
                        labels=self._labels(
                            name, self.deployment.get_space(self.network)
                        ),
                    ),
                    spec={
                        "ipAddressPools": [self.pool],
                        "interfaces": [interface],
                        "nodeSelectors": [
                            {
                                "matchLabels": {
                                    HOSTNAME_LABEL: name,
                                }
                            }
                        ],
                    },
                ),
                field_manager=self.deployment.name,
                force=True,
            )
        except l_exceptions.ApiError as e:
            if e.status.code == 500 and "failed calling webhook" in str(e.status):
                raise tenacity.TryAgain("Trying to patch again")
            raise

    def run(self, context: StepContext) -> Result:
        """Run the step to completion."""
        node_not_found = []

        for node in self.to_update:
            name = node["name"]
            try:
                interface = self._get_interface(node)
            except MachineNotFoundException:
                LOG.debug(
                    "Failed to get the machine for L2 advertisement on %s",
                    name,
                    exc_info=True,
                )
                node_not_found.append(name)
                continue

            try:
                self._ensure_l2_advertisement(name, interface)
            except l_exceptions.ApiError:
                LOG.debug("Failed to update L2 advertisement", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Failed to update L2 advertisement for {name}",
                )

        if node_not_found:
            return Result(
                ResultType.SKIPPED,
                "Failed to get machines for L2 advertisement on nodes: "
                + ", ".join(node_not_found),
            )

        for node in self.to_delete:
            try:
                self.kube.delete(
                    self.l2_advertisement_resource,
                    self._l2_advertisement_name(node["name"]),
                    namespace=self.l2_advertisement_namespace,
                )
            except l_exceptions.ApiError:
                LOG.debug("Failed to delete L2 advertisement", exc_info=True)
                continue

        return Result(ResultType.COMPLETED)
```

Note: `_get_interface(node, network)` calls in L2 become `_get_interface(node)` — the network is now on `self.network` from the base class.

- [ ] **Step 4: Update L2 test calls to match new `_get_interface` signature**

In `test_k8s.py`, the `TestEnsureL2AdvertisementByHostStep` tests that call `step._get_interface({"name": "node1", "machineid": "1"}, network)` need the second argument removed:

```python
# Old:
step._get_interface({"name": "node1"}, network)
step._get_interface({"name": "node1", "machineid": "1"}, network)

# New:
step._get_interface({"name": "node1"})
step._get_interface({"name": "node1", "machineid": "1"})
```

Update these tests:
- `test_get_interface_cached` (line ~472): remove `network` arg
- `test_get_interface_found` (line ~478): remove `network` arg
- `test_get_interface_not_found` (line ~489): remove `network` arg, remove `network.name` setup

For `test_get_interface_not_found`, the error message now uses `self.network.name`. Since the step fixture uses `network = Mock()`, ensure the mock's `name` attribute is set. Since the `step` fixture passes a Mock as network, and `Mock().name` returns the mock's internal name string, explicitly set `network.name = "test-network"` on the mock passed to the step constructor, or check the error message without the network name.

- [ ] **Step 5: Run L2 tests to verify no regressions**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py::TestEnsureL2AdvertisementByHostStep -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 6: Run full test suite**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py -v 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 7: Run linting**

```bash
cd sunbeam-python && tox -e pep8 2>&1 | tail -20
```

Expected: clean pass.

- [ ] **Step 8: Commit**

```bash
git add sunbeam-python/sunbeam/steps/k8s.py sunbeam-python/tests/unit/sunbeam/steps/test_k8s.py && git commit -m "refactor: extract _PerHostK8SResourceStep base class from EnsureL2AdvertisementByHostStep

Shared per-host logic (control node lookup, kube client creation,
interface-by-space lookup, is_skip skeleton) now lives in the base.
Prepares for EnsureCiliumDeviceByHostStep which shares the same pattern."
```

---

### Task 4: Implement `EnsureCiliumDeviceByHostStep`

**Files:**
- Modify: `sunbeam-python/sunbeam/steps/k8s.py`
- Test: `sunbeam-python/tests/unit/sunbeam/steps/test_k8s.py`

- [ ] **Step 1: Write the test class skeleton and `test_is_skip_no_changes`**

Add to the end of `sunbeam-python/tests/unit/sunbeam/steps/test_k8s.py`:

```python
class TestEnsureCiliumDeviceByHostStep:
    @pytest.fixture
    def deployment(self, basic_deployment):
        basic_deployment.name = "test-deployment"
        basic_deployment.get_space.return_value = "internal"
        return basic_deployment

    @pytest.fixture
    def control_nodes(self):
        return [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]

    @pytest.fixture
    def client(self, control_nodes):
        return Mock(
            cluster=Mock(
                list_nodes_by_role=Mock(return_value=control_nodes),
                get_config=Mock(return_value="{}"),
            )
        )

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def step(self, deployment, client, jhelper):
        step = EnsureCiliumDeviceByHostStep(
            deployment, client, jhelper, "test-model"
        )
        step.kube = Mock()
        return step

    @pytest.fixture(autouse=True)
    def setup_patches(self, step):
        kubeconfig_mocker = patch(
            "sunbeam.steps.k8s.l_kubeconfig.KubeConfig",
            Mock(from_dict=Mock(return_value=Mock())),
        )
        kubeconfig_mocker.start()
        kube_mocker = patch(
            "sunbeam.steps.k8s.l_client.Client",
            Mock(return_value=Mock(return_value=step.kube)),
        )
        kube_mocker.start()
        yield
        kubeconfig_mocker.stop()
        kube_mocker.stop()

    def test_is_skip_no_changes(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=([], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED
```

Also add the import at the top of the test file:

```python
from sunbeam.steps.k8s import EnsureCiliumDeviceByHostStep
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py::TestEnsureCiliumDeviceByHostStep::test_is_skip_no_changes -v 2>&1 | tail -10
```

Expected: FAIL — `ImportError: cannot import name 'EnsureCiliumDeviceByHostStep'`

- [ ] **Step 3: Write the minimal `EnsureCiliumDeviceByHostStep` class**

Add to `sunbeam-python/sunbeam/steps/k8s.py`, after the `_PerHostK8SResourceStep` class and before `EnsureL2AdvertisementByHostStep`:

```python
class EnsureCiliumDeviceByHostStep(_PerHostK8SResourceStep):
    """Ensure each control node has a CiliumNodeConfig for its internal-space device.

    Creates or updates a CiliumNodeConfig resource per control node, specifying
    the exact network interface that corresponds to the internal juju space.
    After applying a changed config, the cilium pod on that node is restarted
    to pick up the new device binding.
    """

    _CILIUM_NAMESPACE = "kube-system"
    _CILIUM_POD_LABEL = "k8s-app=cilium"
    _RESTART_TIMEOUT = 300  # seconds
    _RESTART_POLL_INTERVAL = 5  # seconds

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        fqdn: str | None = None,
    ):
        super().__init__(
            "Ensure Cilium device config",
            "Ensuring Cilium device config per host",
            deployment,
            client,
            jhelper,
            model,
            Networks.INTERNAL,
            kube_namespace=self._CILIUM_NAMESPACE,
            fqdn=fqdn,
        )
        self.cilium_node_config_resource = (
            K8SHelper.get_lightkube_cilium_node_config_resource()
        )

    def _cilium_node_config_name(self, hostname: str) -> str:
        """Return CiliumNodeConfig resource name for a node."""
        return f"cilium-devices-{hostname}"

    def _labels(self, hostname: str) -> dict[str, str]:
        """Return labels for the CiliumNodeConfig resource."""
        return {
            "app.kubernetes.io/managed-by": self.deployment.name,
            HOSTNAME_LABEL: hostname,
        }

    def _get_outdated_resources(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        """Get outdated CiliumNodeConfig resources."""
        outdated: list[str] = [node["name"] for node in nodes]
        deleted: list[str] = []

        configs = kube.list(
            self.cilium_node_config_resource,
            namespace=self._CILIUM_NAMESPACE,
            labels={"app.kubernetes.io/managed-by": self.deployment.name},
        )

        for config in configs:
            if config.metadata is None or config.metadata.labels is None:
                LOG.debug("CiliumNodeConfig has no metadata or labels")
                continue
            hostname = config.metadata.labels.get(HOSTNAME_LABEL)
            if hostname is None:
                LOG.debug(
                    "CiliumNodeConfig %s has no hostname label",
                    config.metadata.name,
                )
                continue
            if config.spec is None:
                LOG.debug("CiliumNodeConfig %r has no spec", hostname)
                continue
            if hostname not in outdated:
                LOG.debug(
                    "CiliumNodeConfig %s has no matching node",
                    config.metadata.name,
                )
                deleted.append(hostname)
                continue

            # Validate nodeSelector
            node_selector = config.spec.get("nodeSelector", {})
            match_labels = node_selector.get("matchLabels", {})
            if match_labels.get(HOSTNAME_LABEL) != hostname:
                LOG.debug(
                    "CiliumNodeConfig %s has wrong nodeSelector",
                    config.metadata.name,
                )
                continue

            # Validate device
            defaults = config.spec.get("defaults", {})
            interface = None
            for node in nodes:
                if node["name"] == hostname:
                    interface = self._get_interface(node)
            if not interface:
                LOG.debug(
                    "CiliumNodeConfig %s: no interface for node",
                    config.metadata.name,
                )
                continue
            if defaults.get("devices") != interface:
                LOG.debug(
                    "CiliumNodeConfig %s has wrong device (got %s, want %s)",
                    config.metadata.name,
                    defaults.get("devices"),
                    interface,
                )
                continue

            outdated.remove(hostname)
        return outdated, deleted

    def _find_cilium_pod(self, node_name: str) -> "core_v1.Pod":
        """Find the cilium pod running on the given node."""
        pods = list(
            self.kube.list(
                core_v1.Pod,
                namespace=self._CILIUM_NAMESPACE,
                labels={"k8s-app": "cilium"},
            )
        )
        for pod in pods:
            if pod.spec and pod.spec.nodeName == node_name:
                return pod
        raise SunbeamException(
            f"No cilium pod found on node {node_name}"
        )

    def _wait_for_cilium_ready(self, node_name: str) -> None:
        """Wait until a Ready cilium pod exists on the given node."""
        deadline = time.monotonic() + self._RESTART_TIMEOUT
        while time.monotonic() < deadline:
            try:
                pods = list(
                    self.kube.list(
                        core_v1.Pod,
                        namespace=self._CILIUM_NAMESPACE,
                        labels={"k8s-app": "cilium"},
                    )
                )
            except l_exceptions.ApiError as e:
                raise SunbeamException(
                    f"Failed to list cilium pods during restart wait: {e}"
                ) from e

            for pod in pods:
                if pod.spec and pod.spec.nodeName == node_name:
                    if pod.status and pod.status.conditions:
                        for condition in pod.status.conditions:
                            if (
                                condition.type == "Ready"
                                and condition.status == "True"
                            ):
                                LOG.debug(
                                    "Cilium pod on %s is Ready", node_name
                                )
                                return
            LOG.debug("Waiting for cilium pod on %s to be Ready", node_name)
            time.sleep(self._RESTART_POLL_INTERVAL)

        raise SunbeamException(
            f"Cilium pod on {node_name} did not become Ready "
            f"within {self._RESTART_TIMEOUT}s"
        )

    def _restart_cilium_on_node(self, node_name: str) -> None:
        """Delete the cilium pod on a node and wait for the replacement."""
        pod = self._find_cilium_pod(node_name)
        pod_name = pod.metadata.name if pod.metadata else "unknown"
        LOG.debug("Deleting cilium pod %s on node %s", pod_name, node_name)
        self.kube.delete(
            core_v1.Pod, pod_name, namespace=self._CILIUM_NAMESPACE
        )
        self._wait_for_cilium_ready(node_name)

    def run(self, context: StepContext) -> Result:
        """Apply CiliumNodeConfig per node and restart affected cilium pods."""
        for node in self.to_update:
            name = node["name"]
            try:
                interface = self._get_interface(node)
            except MachineNotFoundException:
                LOG.debug(
                    "Failed to get machine for CiliumNodeConfig on %s",
                    name,
                    exc_info=True,
                )
                return Result(
                    ResultType.FAILED,
                    f"Machine not found for node {name}",
                )

            try:
                self.kube.apply(
                    self.cilium_node_config_resource(
                        metadata=meta_v1.ObjectMeta(
                            name=self._cilium_node_config_name(name),
                            labels=self._labels(name),
                        ),
                        spec={
                            "nodeSelector": {
                                "matchLabels": {
                                    HOSTNAME_LABEL: name,
                                },
                            },
                            "defaults": {
                                "devices": interface,
                            },
                        },
                    ),
                    field_manager=self.deployment.name,
                    force=True,
                )
            except l_exceptions.ApiError:
                LOG.debug("Failed to apply CiliumNodeConfig", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Failed to apply CiliumNodeConfig for {name}",
                )

            try:
                self._restart_cilium_on_node(name)
            except SunbeamException as e:
                return Result(ResultType.FAILED, str(e))

        for node in self.to_delete:
            name = node["name"]
            try:
                self.kube.delete(
                    self.cilium_node_config_resource,
                    self._cilium_node_config_name(name),
                    namespace=self._CILIUM_NAMESPACE,
                )
            except l_exceptions.ApiError:
                LOG.debug(
                    "Failed to delete CiliumNodeConfig for %s", name,
                    exc_info=True,
                )
                continue

            try:
                self._restart_cilium_on_node(name)
            except SunbeamException:
                LOG.debug(
                    "Failed to restart cilium on %s after config deletion",
                    name,
                    exc_info=True,
                )
                continue

        return Result(ResultType.COMPLETED)
```

- [ ] **Step 4: Run the first test to verify it passes**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py::TestEnsureCiliumDeviceByHostStep::test_is_skip_no_changes -v 2>&1 | tail -10
```

Expected: PASS

- [ ] **Step 5: Add remaining `is_skip` tests**

Append to `TestEnsureCiliumDeviceByHostStep` in `test_k8s.py`:

```python
    def test_is_skip_outdated_device(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=(["node1"], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 1
        assert step.to_update[0]["name"] == "node1"

    def test_is_skip_missing_config(self, step, step_context):
        """Node with no CiliumNodeConfig yet is reported as outdated."""
        step._get_outdated_resources = Mock(return_value=(["node1", "node2"], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 2

    def test_is_skip_deleted_node(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=([], ["node2"]))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_delete) == 1

    def test_is_skip_single_node_fqdn(self, deployment, client, jhelper, step_context):
        node_info = {"name": "node1", "machineid": "1", "role": ["control"]}
        client.cluster.get_node_info = Mock(return_value=node_info)
        step = EnsureCiliumDeviceByHostStep(
            deployment, client, jhelper, "test-model", fqdn="node1.maas"
        )
        step.kube = Mock()
        step._get_outdated_resources = Mock(return_value=(["node1"], []))
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert step.control_nodes == [node_info]

    def test_is_skip_wrong_node_selector(self, step, control_nodes, jhelper):
        """Config with right label/device but wrong nodeSelector is outdated."""
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="internal"),
        }
        wrong_selector_config = Mock()
        wrong_selector_config.metadata = Mock(
            name="cilium-devices-node1",
            labels={
                "app.kubernetes.io/managed-by": "test-deployment",
                "sunbeam/hostname": "node1",
            },
        )
        wrong_selector_config.spec = {
            "nodeSelector": {"matchLabels": {"sunbeam/hostname": "wrong-node"}},
            "defaults": {"devices": "eth0"},
        }
        step.kube.list = Mock(return_value=[wrong_selector_config])
        outdated, deleted = step._get_outdated_resources(control_nodes, step.kube)
        assert "node1" in outdated
```

- [ ] **Step 6: Run the is_skip tests**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py::TestEnsureCiliumDeviceByHostStep -k "is_skip" -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 7: Add `run` tests**

Append to `TestEnsureCiliumDeviceByHostStep`:

```python
    def test_run_creates_config(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step.kube.apply = Mock()

        cilium_pod = Mock()
        cilium_pod.metadata = Mock(name="cilium-abc")
        cilium_pod.spec = Mock(nodeName="node1")
        cilium_pod.status = Mock(
            conditions=[Mock(type="Ready", status="True")]
        )
        step.kube.list = Mock(return_value=[cilium_pod])
        step.kube.delete = Mock()

        result = step.run(None)

        step.kube.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_updates_config(self, step):
        step.to_update = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        step.to_delete = []
        step._get_interface = Mock(side_effect=["eth0", "eth1"])
        step.kube.apply = Mock()

        pod1 = Mock()
        pod1.metadata = Mock(name="cilium-aaa")
        pod1.spec = Mock(nodeName="node1")
        pod1.status = Mock(conditions=[Mock(type="Ready", status="True")])
        pod2 = Mock()
        pod2.metadata = Mock(name="cilium-bbb")
        pod2.spec = Mock(nodeName="node2")
        pod2.status = Mock(conditions=[Mock(type="Ready", status="True")])
        step.kube.list = Mock(return_value=[pod1, pod2])
        step.kube.delete = Mock()

        result = step.run(None)

        assert step.kube.apply.call_count == 2
        assert result.result_type == ResultType.COMPLETED

    def test_run_deletes_stale_config(self, step):
        step.to_update = []
        step.to_delete = [{"name": "node2", "machineid": "2"}]
        step.kube.delete = Mock()

        cilium_pod = Mock()
        cilium_pod.metadata = Mock(name="cilium-xyz")
        cilium_pod.spec = Mock(nodeName="node2")
        cilium_pod.status = Mock(
            conditions=[Mock(type="Ready", status="True")]
        )
        step.kube.list = Mock(return_value=[cilium_pod])

        result = step.run(None)

        assert step.kube.delete.call_count == 2  # config + pod
        assert result.result_type == ResultType.COMPLETED

    def test_run_api_error(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.apply = Mock(side_effect=api_error)

        result = step.run(None)

        assert result.result_type == ResultType.FAILED
        assert "Failed to apply CiliumNodeConfig for node1" in result.message

    def test_run_no_interface_found(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(
            side_effect=MachineNotFoundException("not found")
        )

        result = step.run(None)

        assert result.result_type == ResultType.FAILED

    def test_run_cilium_pod_not_found(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step.kube.apply = Mock()
        step.kube.list = Mock(return_value=[])  # no cilium pods

        result = step.run(None)

        assert result.result_type == ResultType.FAILED
        assert "No cilium pod found on node node1" in result.message

    def test_run_restart_timeout(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step.kube.apply = Mock()

        # _find_cilium_pod succeeds
        cilium_pod = Mock()
        cilium_pod.metadata = Mock(name="cilium-abc")
        cilium_pod.spec = Mock(nodeName="node1")
        # But replacement never becomes Ready
        not_ready_pod = Mock()
        not_ready_pod.spec = Mock(nodeName="node1")
        not_ready_pod.status = Mock(
            conditions=[Mock(type="Ready", status="False")]
        )

        call_count = [0]
        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: _find_cilium_pod
                return [cilium_pod]
            # Subsequent calls: _wait_for_cilium_ready
            return [not_ready_pod]

        step.kube.list = Mock(side_effect=list_side_effect)
        step.kube.delete = Mock()

        with (
            patch("sunbeam.steps.k8s.time.monotonic", side_effect=[0.0, 301.0]),
            patch("sunbeam.steps.k8s.time.sleep"),
        ):
            result = step.run(None)

        assert result.result_type == ResultType.FAILED
        assert "did not become Ready" in result.message
```

- [ ] **Step 8: Run all cilium tests**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py::TestEnsureCiliumDeviceByHostStep -v 2>&1 | tail -25
```

Expected: all pass.

- [ ] **Step 9: Run full test suite and linting**

```bash
cd sunbeam-python && python -m pytest tests/unit/sunbeam/steps/test_k8s.py -v 2>&1 | tail -30
```

```bash
cd sunbeam-python && tox -e pep8 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add sunbeam-python/sunbeam/steps/k8s.py sunbeam-python/tests/unit/sunbeam/steps/test_k8s.py && git commit -m "feat: add EnsureCiliumDeviceByHostStep for per-host CiliumNodeConfig

Creates a CiliumNodeConfig resource per control node, specifying
the exact internal-space NIC. Validates nodeSelector, device, and
hostname label to detect drift. Restarts only affected cilium pods
and waits for readiness before proceeding."
```

---

### Task 5: Integrate into command plans

**Files:**
- Modify: `sunbeam-python/sunbeam/provider/local/commands.py`
- Modify: `sunbeam-python/sunbeam/provider/maas/commands.py`
- Modify: `sunbeam-python/sunbeam/steps/upgrades/intra_channel.py`

- [ ] **Step 1: Add import to local commands**

In `sunbeam-python/sunbeam/provider/local/commands.py`, add `EnsureCiliumDeviceByHostStep` to the import from `sunbeam.steps.k8s` (around line 155-164):

```python
from sunbeam.steps.k8s import (
    ...
    EnsureCiliumDeviceByHostStep,
    EnsureDefaultL2AdvertisementMutedStep,
    ...
)
```

- [ ] **Step 2: Add to local bootstrap plan**

In `sunbeam-python/sunbeam/provider/local/commands.py`, before the `EnsureL2AdvertisementByHostStep` call (around line 312), add:

```python
            EnsureCiliumDeviceByHostStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
                fqdn,
            ),
```

- [ ] **Step 3: Add to local add-node plan**

In `sunbeam-python/sunbeam/provider/local/commands.py`, before the `EnsureL2AdvertisementByHostStep` call (around line 1520), add:

```python
        plan4.append(
            EnsureCiliumDeviceByHostStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
                name,
            ),
        )
```

- [ ] **Step 4: Add to local resize plan**

In `sunbeam-python/sunbeam/provider/local/commands.py`, before the `EnsureL2AdvertisementByHostStep` call (around line 1885), add:

```python
            EnsureCiliumDeviceByHostStep(
                deployment,
                client,
                jhelper,
                deployment.openstack_machines_model,
            ),
```

- [ ] **Step 5: Add import to MAAS commands**

In `sunbeam-python/sunbeam/provider/maas/commands.py`, add `EnsureCiliumDeviceByHostStep` to the import from `sunbeam.steps.k8s` (around line 160-169):

```python
from sunbeam.steps.k8s import (
    ...
    EnsureCiliumDeviceByHostStep,
    EnsureDefaultL2AdvertisementMutedStep,
    ...
)
```

- [ ] **Step 6: Add to MAAS bootstrap plan**

In `sunbeam-python/sunbeam/provider/maas/commands.py`, before the first `EnsureL2AdvertisementByHostStep` call (around line 752), add:

```python
    plan2.append(
        EnsureCiliumDeviceByHostStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
        ),
    )
```

- [ ] **Step 7: Add to MAAS remove-node plan**

In `sunbeam-python/sunbeam/provider/maas/commands.py`, before the `EnsureL2AdvertisementByHostStep` calls (around line 1678), add:

```python
        EnsureCiliumDeviceByHostStep(
            deployment,
            client,
            jhelper,
            deployment.openstack_machines_model,
        ),
```

- [ ] **Step 8: Update upgrade steps**

In `sunbeam-python/sunbeam/steps/upgrades/intra_channel.py`:

Update the import (around line 32-37) — remove `EnsureCiliumOnCorrectSpaceStep`, add `EnsureCiliumDeviceByHostStep`:

```python
from sunbeam.steps.k8s import (
    DeployK8SApplicationStep,
    EnsureCiliumDeviceByHostStep,
    EnsureDefaultL2AdvertisementMutedStep,
    EnsureL2AdvertisementByHostStep,
)
```

Replace the `EnsureCiliumOnCorrectSpaceStep(...)` call in the MAAS branch (around line 459) with:

```python
                    EnsureCiliumDeviceByHostStep(
                        self.deployment,
                        self.client,
                        self.jhelper,
                        self.deployment.openstack_machines_model,
                    ),
```

Replace the `EnsureCiliumOnCorrectSpaceStep(...)` call in the local branch (around line 508) with:

```python
                    EnsureCiliumDeviceByHostStep(
                        self.deployment,
                        self.client,
                        self.jhelper,
                        self.deployment.openstack_machines_model,
                    ),
```

- [ ] **Step 9: Run linting**

```bash
cd sunbeam-python && tox -e pep8 2>&1 | tail -20
```

Expected: clean pass.

- [ ] **Step 10: Run full test suite**

```bash
cd sunbeam-python && python -m pytest tests/unit/ -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add sunbeam-python/sunbeam/provider/local/commands.py sunbeam-python/sunbeam/provider/maas/commands.py sunbeam-python/sunbeam/steps/upgrades/intra_channel.py && git commit -m "feat: integrate EnsureCiliumDeviceByHostStep into all command plans

Placed before EnsureL2AdvertisementByHostStep in local bootstrap,
add-node, resize plans; MAAS bootstrap and remove-node plans; and
upgrade steps (replacing EnsureCiliumOnCorrectSpaceStep)."
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full test suite**

```bash
cd sunbeam-python && python -m pytest tests/unit/ -v 2>&1 | tail -40
```

Expected: all pass.

- [ ] **Step 2: Run linting and type checks**

```bash
cd sunbeam-python && tox -e pep8 2>&1 | tail -20
```

```bash
cd sunbeam-python && tox -e mypy 2>&1 | tail -20
```

Expected: both clean.

- [ ] **Step 3: Verify no stale references**

```bash
cd sunbeam-python && grep -rn "CILIUM_DEVICES_ANNOTATION\|EnsureCiliumOnCorrectSpaceStep\|_get_cluster_ips" sunbeam/ tests/ || echo "No stale references"
```

Expected: "No stale references"

- [ ] **Step 4: Review the diff**

```bash
git diff --stat HEAD~5
```

Verify only the expected files are modified.
