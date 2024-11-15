# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import grp
import json
import os
from unittest.mock import Mock

from sunbeam.core import checks


class TestSshKeysConnectedCheck:
    def test_run(self, mocker, snap):
        snap_ctl = Mock()
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch.object(checks, "SnapCtl", return_value=snap_ctl)

        check = checks.SshKeysConnectedCheck()

        result = check.run()

        assert result is True

    def test_run_missing_interface(self, mocker, snap):
        snap_ctl = Mock(is_connected=Mock(return_value=False))
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch.object(checks, "SnapCtl", return_value=snap_ctl)

        check = checks.SshKeysConnectedCheck()

        result = check.run()

        assert result is False
        assert f"sudo snap connect {snap.name}:ssh-keys" in check.message


class TestDaemonGroupCheck:
    def test_run(self, mocker, snap):
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch.object(os, "access", return_value=True)

        check = checks.DaemonGroupCheck()

        result = check.run()

        assert result is True

    def test_run_no_daemon_socket_access(self, mocker, snap):
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch.object(os, "access", return_value=False)

        check = checks.DaemonGroupCheck()

        result = check.run()

        assert result is False
        assert "Insufficient permissions" in check.message


class TestLocalShareCheck:
    def test_run(self, mocker, snap):
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch("os.path.exists", return_value=True)

        check = checks.LocalShareCheck()

        result = check.run()

        assert result is True
        os.path.exists.assert_called_with(snap.paths.real_home / ".local/share")

    def test_run_missing(self, mocker, snap):
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch("os.path.exists", return_value=False)

        check = checks.LocalShareCheck()

        result = check.run()

        assert result is False
        assert "directory not detected" in check.message
        os.path.exists.assert_called_with(snap.paths.real_home / ".local/share")


class TestVerifyFQDNCheck:
    def test_run(self):
        name = "myhost.mydomain.net"
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is True

    def test_run_hostname_fqdn(self):
        name = "myhost."
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is True

    def test_run_hostname_pqdn(self):
        name = "myhost"
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is False

    def test_run_fqdn_invalid_character(self):
        name = "myhost.mydomain.net!"
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is False

    def test_run_fqdn_starts_with_hyphen(self):
        name = "-myhost.mydomain.net"
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is False

    def test_run_fqdn_starts_with_dot(self):
        name = ".myhost.mydomain.net"
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is False

    def test_run_fqdn_too_long(self):
        name = "myhost.mydomain.net" * 50
        check = checks.VerifyFQDNCheck(name)

        result = check.run()

        assert result is False


class TestSystemRequirementsCheck:
    error_message = (
        "WARNING: Minimum system requirements (4 core CPU, 16 GB RAM) not met."
    )

    def test_run(self, mocker):
        mocker.patch(
            "sunbeam.core.checks.get_host_total_ram", return_value=16 * 1024 * 1024
        )
        mocker.patch("sunbeam.core.checks.get_host_total_cores", return_value=4)
        check = checks.SystemRequirementsCheck()

        result = check.run()

        assert result is True

    def test_run_less_than_16GB_RAM(self, mocker):
        mocker.patch(
            "sunbeam.core.checks.get_host_total_ram", return_value=8 * 1024 * 1024
        )
        mocker.patch("sunbeam.core.checks.get_host_total_cores", return_value=4)
        check = checks.SystemRequirementsCheck()

        result = check.run()

        assert check.message == self.error_message
        assert result is True

    def test_run_less_than_4_cores(self, mocker):
        mocker.patch(
            "sunbeam.core.checks.get_host_total_ram", return_value=16 * 1024 * 1024
        )
        mocker.patch("sunbeam.core.checks.get_host_total_cores", return_value=2)
        check = checks.SystemRequirementsCheck()

        result = check.run()

        assert check.message == self.error_message
        assert result is True

    def test_run_more_than_16GB_RAM(self, mocker):
        mocker.patch(
            "sunbeam.core.checks.get_host_total_ram", return_value=32 * 1024 * 1024
        )
        mocker.patch("sunbeam.core.checks.get_host_total_cores", return_value=4)
        check = checks.SystemRequirementsCheck()

        result = check.run()

        assert result is True

    def test_run_more_than_4_cores(self, mocker):
        mocker.patch(
            "sunbeam.core.checks.get_host_total_ram", return_value=16 * 1024 * 1024
        )
        mocker.patch("sunbeam.core.checks.get_host_total_cores", return_value=8)
        check = checks.SystemRequirementsCheck()

        result = check.run()

        assert result is True


class TestTokenCheck:
    def test_run_empty_token(self):
        token = ""
        check = checks.TokenCheck(token)

        result = check.run()

        assert result is False
        assert "empty string" in check.message

    def test_run_invalid_base64_token(self):
        token = "Abb+Ckfr\\01=!!!"
        check = checks.TokenCheck(token)

        result = check.run()

        assert result is False
        assert "not a valid base64 string" in check.message

    def test_run_invalid_json_token(self):
        token = b"{invalid_json}"

        check = checks.TokenCheck(base64.b64encode(token).decode())

        result = check.run()

        assert result is False
        assert "not a valid JSON-encoded object" in check.message

    def test_run_invalid_json_object_token(self):
        token = b'["my_list"]'
        check = checks.TokenCheck(base64.b64encode(token).decode())

        result = check.run()

        assert result is False
        assert "not a valid JSON object" in check.message

    def test_run_missing_required_fields(self):
        token = json.dumps({"name": "myname"})
        check = checks.TokenCheck(base64.b64encode(token.encode()).decode())

        result = check.run()

        assert result is False
        assert "fingerprint, join_addresses, secret" in check.message

    def test_run_empty_join_addresses(self):
        token = json.dumps(
            {
                "secret": "mysecret",
                "join_addresses": [],
                "fingerprint": "123",
            }
        )

        check = checks.TokenCheck(base64.b64encode(token.encode()).decode())

        result = check.run()

        assert result is False
        assert check.message == "Join token 'join_addresses' is empty"

    def test_run_valid_token(self):
        token = json.dumps(
            {
                "secret": "mysecret",
                "join_addresses": ["address"],
                "fingerprint": "123",
            }
        )
        check = checks.TokenCheck(base64.b64encode(token.encode()).decode())

        result = check.run()

        assert result is True


class TestLxdGroupCheck:
    def test_user_in_lxd_group(self, mocker):
        user = os.environ.get("USER")
        grp_struct = Mock()
        mocker.patch.object(grp, "getgrnam", return_value=grp_struct)
        grp_struct.gr_mem = [user]

        check = checks.LxdGroupCheck()
        result = check.run()

        assert result is True

    def test_user_not_in_lxd_group(self, mocker):
        user = os.environ.get("USER")
        grp_struct = Mock()
        mocker.patch.object(grp, "getgrnam", return_value=grp_struct)
        grp_struct.gr_mem = []

        check = checks.LxdGroupCheck()
        result = check.run()

        assert result is False
        assert f"{user} not part of lxd group" in check.message


class TestLxdControllerRegistrationCheck:
    def test_lxd_controller_exists(self, mocker):
        jsh = Mock()
        mocker.patch.object(checks, "JujuStepHelper", return_value=jsh)
        jsh.get_controllers.return_value = ["lxdcloud"]

        check = checks.LXDJujuControllerRegistrationCheck()
        result = check.run()

        assert result is True

    def test_lxd_controller_does_not_exist(self, mocker):
        jsh = Mock()
        mocker.patch.object(checks, "JujuStepHelper", return_value=jsh)
        jsh.get_controllers.return_value = []

        check = checks.LXDJujuControllerRegistrationCheck()
        result = check.run()

        assert result is False
        assert "Missing Juju controller on LXD" in check.message
