# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import MagicMock, Mock, patch

import jubilant
import pytest
import yaml

import sunbeam.core.juju as jujulib

kubeconfig_yaml = """
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUREekNDQWZlZ0F3SUJBZ0lVSDh2MmtKZDE0TEs4VWIrM1RmUGVUY21pMWNrd0RRWUpLb1pJaHZjTkFRRUwKQlFBd0Z6RVZNQk1HQTFVRUF3d01NVEF1TVRVeUxqRTRNeTR4TUI0WERUSXpNRFF3TkRBMU1Ua3lOVm9YRFRNegpNRFF3TVRBMU1Ua3lOVm93RnpFVk1CTUdBMVVFQXd3TU1UQXVNVFV5TGpFNE15NHhNSUlCSWpBTkJna3Foa2lHCjl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUF4RWkwVFhldmJYNFNvZ2VsRW16T0NQU2tYNHloOURCVGd6WFEKQkdJQTF4TDFwZ09mRkNMNzZYSlROSU4rYUNPT1BoVGp6dXoyR3dpR05pMHVBdnZyUGVrN0p0cEliUjg4YjRSQQpZUTRtMTllMU5zVjdwZ2pHL0JEQzVza1dycVpoZTR5ZTZoOXI2OXpKb1l5NEE4eFZLb1MvdElBZkdSejZvaS9uCndpY0ZzKzQyc29icm92MFdyUm5KbFV4eisyVHB2TFA1TW40eUExZHpGV0RLMTVCemVHa1YyYTVDeHBqcFBBTE4KVzUwVWlvSittbHBmTmwvYzZKWmFaZDR4S1NxclppU2dCY3BOQlhvWjJYVHpDOVNJTFF5RGZpZUpVNWxOcEIwSgpvSUphT0UvOTNseGp1bUdsSlRLSS9ucmpYM241UDFyaFFlWTNxV2p5S21ZNlFucjRqUUlEQVFBQm8xTXdVVEFkCkJnTlZIUTRFRmdRVU0yVTBMSTZtcGFaOTVkTnlIRGs1ZlZCck5ISXdId1lEVlIwakJCZ3dGb0FVTTJVMExJNm0KcGFaOTVkTnlIRGs1ZlZCck5ISXdEd1lEVlIwVEFRSC9CQVV3QXdFQi96QU5CZ2txaGtpRzl3MEJBUXNGQUFPQwpBUUVBZzZITWk4eTQrSENrOCtlb1FuamlmOHd4MytHVDZFNk02SWdRWWRvSFJjYXNYZ0JWLzd6OVRHQnpNeG1aCmdrL0Fnc08yQitLUFh3NmdQZU1GL1JLMjhGNlovK0FjYWMzdUtjT1N1WUJiL2lRKzI1cU9BazZaTStoSTVxMWQKUm1uVzBIQmpzNmg1bVlDODJrSVcrWStEYWN5bUx3OTF3S2ptTXlvMnh4OTBRb0IvWnBSVUxiNjVvWmlkcHZEawpOMStleFg4QmhIeE85S0lhMFFvcThVWFdLTjN4anZRb1pVanFieXY1VWFvcjBwbWpKT1NLKzJLMllRSk9FbUxaCkFDdEtzUDNpaU1UTlRXYUpxVjdWUVZaL3dRUVdsQ1h3VFp3WGlicXk0Z0kwb3JrcVNha0gzVFZMblVrRlFKU24KUi8waU1RRVFzQW5kajZBcVhlQml3ZG5aSGc9PQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0tCg==  # noqa: E501
    server: https://10.5.1.180:16443
  name: k8s-cluster
contexts:
- context:
    cluster: k8s-cluster
    user: admin
  name: k8s
current-context: k8s
kind: Config
preferences: {}
users:
- name: admin
  user:
    token: FAKETOKEN
"""

kubeconfig_clientcertificate_yaml = """
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUREekNDQWZlZ0F3SUJBZ0lVSDh2MmtKZDE0TEs4VWIrM1RmUGVUY21pMWNrd0RRWUpLb1pJaHZjTkFRRUwKQlFBd0Z6RVZNQk1HQTFVRUF3d01NVEF1TVRVeUxqRTRNeTR4TUI0WERUSXpNRFF3TkRBMU1Ua3lOVm9YRFRNegpNRFF3TVRBMU1Ua3lOVm93RnpFVk1CTUdBMVVFQXd3TU1UQXVNVFV5TGpFNE15NHhNSUlCSWpBTkJna3Foa2lHCjl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUF4RWkwVFhldmJYNFNvZ2VsRW16T0NQU2tYNHloOURCVGd6WFEKQkdJQTF4TDFwZ09mRkNMNzZYSlROSU4rYUNPT1BoVGp6dXoyR3dpR05pMHVBdnZyUGVrN0p0cEliUjg4YjRSQQpZUTRtMTllMU5zVjdwZ2pHL0JEQzVza1dycVpoZTR5ZTZoOXI2OXpKb1l5NEE4eFZLb1MvdElBZkdSejZvaS9uCndpY0ZzKzQyc29icm92MFdyUm5KbFV4eisyVHB2TFA1TW40eUExZHpGV0RLMTVCemVHa1YyYTVDeHBqcFBBTE4KVzUwVWlvSittbHBmTmwvYzZKWmFaZDR4S1NxclppU2dCY3BOQlhvWjJYVHpDOVNJTFF5RGZpZUpVNWxOcEIwSgpvSUphT0UvOTNseGp1bUdsSlRLSS9ucmpYM241UDFyaFFlWTNxV2p5S21ZNlFucjRqUUlEQVFBQm8xTXdVVEFkCkJnTlZIUTRFRmdRVU0yVTBMSTZtcGFaOTVkTnlIRGs1ZlZCck5ISXdId1lEVlIwakJCZ3dGb0FVTTJVMExJNm0KcGFaOTVkTnlIRGs1ZlZCck5ISXdEd1lEVlIwVEFRSC9CQVV3QXdFQi96QU5CZ2txaGtpRzl3MEJBUXNGQUFPQwpBUUVBZzZITWk4eTQrSENrOCtlb1FuamlmOHd4MytHVDZFNk02SWdRWWRvSFJjYXNYZ0JWLzd6OVRHQnpNeG1aCmdrL0Fnc08yQitLUFh3NmdQZU1GL1JLMjhGNlovK0FjYWMzdUtjT1N1WUJiL2lRKzI1cU9BazZaTStoSTVxMWQKUm1uVzBIQmpzNmg1bVlDODJrSVcrWStEYWN5bUx3OTF3S2ptTXlvMnh4OTBRb0IvWnBSVUxiNjVvWmlkcHZEawpOMStleFg4QmhIeE85S0lhMFFvcThVWFdLTjN4anZRb1pVanFieXY1VWFvcjBwbWpKT1NLKzJLMllRSk9FbUxaCkFDdEtzUDNpaU1UTlRXYUpxVjdWUVZaL3dRUVdsQ1h3VFp3WGlicXk0Z0kwb3JrcVNha0gzVFZMblVrRlFKU24KUi8waU1RRVFzQW5kajZBcVhlQml3ZG5aSGc9PQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0tCg==  # noqa: E501
    server: https://10.5.1.180:16443
  name: k8s-cluster
contexts:
- context:
    cluster: k8s-cluster
    user: admin
  name: k8s
current-context: k8s
kind: Config
preferences: {}
users:
- name: admin
  user:
    client-certificate-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUN6RENDQWJTZ0F3SUJBZ0lVR09YQ3hJNWEybW5vd25wbUpaNU9zVzFHM3FZd0RRWUpLb1pJaHZjTkFRRUwKQlFBd0Z6RVZNQk1HQTFVRUF3d01NVEF1TVRVeUxqRTRNeTR4TUI0WERUSXpNVEF3TkRBeU1EQXlPRm9YRFRNegpNVEF3TVRBeU1EQXlPRm93S1RFT01Bd0dBMVVFQXd3RllXUnRhVzR4RnpBVkJnTlZCQW9NRG5ONWMzUmxiVHB0CllYTjBaWEp6TUlJQklqQU5CZ2txaGtpRzl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUFuZS9YSEppaThraDcKRVA3blkrWEQxOTU1eERVdm5vRGxMNDl6eUEzOGpUNm1pNFZjSzNIRVpxSGpCZzdUeng0ZGJ2OVhNdzdEQjRxWApMRERydWZJa0wrL3BnWm0wT0ozVFpLdU02Z040ZG0vR2M5aHpBbVdoaVplL29jS3pXRmgyVGV0MGJFQ1pQVDNtCmZ5bmZuZ1ZKQzVSNXJpeTFER2t3bHNWQWhQQUxwa0JEb3l0Nkozc0t1QnlJOTB2NTNucTBUSnNkVDFXZzVlelUKZkV0SnZDQ0FOVnFPbThwSmFXRHlmNkF0emNCUytNRHJZdGVrNTlacFFad2VXeU1xQlhVaHdSSnJLNU9jcklTOAp1SlFFL2EwUDVrTmsyRUQwazFZcU4vZVlhWnZXY1RnMkdTK3NWL1luN05oWWlHVm1VMmg0OFRqOGpyRlZsd1ZYCnFaTlFCR3NTcFFJREFRQUJNQTBHQ1NxR1NJYjNEUUVCQ3dVQUE0SUJBUUJEL3gxdndZMXRmR0g0aEY1S1FobFQKdEZQOVFFYWxwam1TOUxtMFo3TDhLY1BlRkRiczRDaW4xbEE4VHdEVEJTTXlpWXZOZEFoR2NOZTJiVHl5eVR5Uwp5KzErT3l1clZrN0hsWG9McWhHczA5c2tTY3hzc1E0QnNKWThweHdYeXpaZUYyL3JMelpkc0x5dVN6VHNOMFo5Cm9CR21Bb2RZMnFHMHVENUZyMTEvS0tRQVdPQlE3M3NGMDhRZDJqVmpudXB1SHd2Y2o5OXByVFRoeExUNG9pc2MKL3QyU3JFdlJMOVlITW5tbnBOdEpZMjhFMUUxeFBUR1orcG8zNzcyUGxVN2ZwVXM4eksrVFlDeXFkaUtnSnJPZQpLR0xKMUVRY3A3YTRYSXIvVzZSRTA0MjB0RUVwNlN4UVJ3cDdJLzBOR0VSSXE4QjVVdjBFYVFtR2xYTzFGbTN4Ci0tLS0tRU5EIENFUlRJRklDQVRFLS0tLS0K
    client-key-data: LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQpNSUlFb2dJQkFBS0NBUUVBbmUvWEhKaWk4a2g3RVA3blkrWEQxOTU1eERVdm5vRGxMNDl6eUEzOGpUNm1pNFZjCkszSEVacUhqQmc3VHp4NGRidjlYTXc3REI0cVhMRERydWZJa0wrL3BnWm0wT0ozVFpLdU02Z040ZG0vR2M5aHoKQW1XaGlaZS9vY0t6V0ZoMlRldDBiRUNaUFQzbWZ5bmZuZ1ZKQzVSNXJpeTFER2t3bHNWQWhQQUxwa0JEb3l0NgpKM3NLdUJ5STkwdjUzbnEwVEpzZFQxV2c1ZXpVZkV0SnZDQ0FOVnFPbThwSmFXRHlmNkF0emNCUytNRHJZdGVrCjU5WnBRWndlV3lNcUJYVWh3UkpySzVPY3JJUzh1SlFFL2EwUDVrTmsyRUQwazFZcU4vZVlhWnZXY1RnMkdTK3MKVi9ZbjdOaFlpR1ZtVTJoNDhUajhqckZWbHdWWHFaTlFCR3NTcFFJREFRQUJBb0lCQUU5amt4N0Z2d3JJMGt2Tgp4aVJhQjZMSUt5OHNpUDVFem0ra3pVOWZjSGJUYWtZeHlBM3lod1lNRkNFa2JPWHN2bURnSzBYNEFxTVUwRDZmCmJLNndmKzQweTR5ZzVZMmNEL25IbmZLM3dlTE85dE9lbHRrNm13T2Q2dTcxL3M3RzBOa0VKU2FSSmpZNW1sYUwKaHVOWXhzbnlYV1BuQnk3dzVVSzBibVVrZ01hVk51OW5JQ1pRTklyMzhQN2VxR1FJQzF3WTJCVjRSWXMzUkh6bAphMW5KeUlqTEJ3bFR4QURBdVJrMWlCNFNaMmdhRWhUNmx4cEhkRmRuVGFwQS9kdXJpY2FHaDBRZmtHL1I4R2VPCkoySHdKRzNhM2R4aHZpMXhpc2hjeUF6Ty9XUmtPSXh2SDNrL3crRjF4dXVKZUtrczVCSDRyYlNJanVsdzFPcWgKdVF2QjhyVUNnWUVBenRiUTdrOFQwUEU3NWxuRjVTTnlwZXNWZHN0TDIwdUtGYWpVSVhBbkZvNHI0R09XZWdMUQpSQUNnd3FwMWFjSnFNT1p6Q2hpd1M1M28vek1TakpGZllSaENqR0s3WVF5bEQ5WklWYzg3bnJEWnZtL2p5SE9pCkxTRHpSSmRDYnJWdmR0SXA2NzRUem5MRmlwSlR1Z3EzL3BQNjhsTm5VR2JLYURuMWZVbjBoc3NDZ1lFQXczbU4Kb09hODZ1aGI1Q3hGbkZvN2h6b0ZQN1A1aGNxbklldW9SS2pJUWpYMFhFdC8wdERiNS9LV0lIQTJzK0k4d2FDSAoyblJsRkZDN1ErSzhGc3JlVDVGTlo1S0cxY3BVNERNbUJzcDdEMU0rUmtkTExadE5rS3R3UjJna2g1OXNTOHVaCkowTEk4L0J4cDRYK0hwOFRSTTY5WHN0QXF4VnNHWUVzaWtDWExrOENnWUFDTm1BRHZJck11RmZZcmVzaytVMFgKb3owV2lUUWxnMWhWeFBtSDVnZzFBSTVObHlNYjZQM0xUR3ByeXFENDRhQjdKMnZobHNRRCt3dHI5MkxpYUFlcQpKVFZKQlNGVjkybW9rclV4WGNjWWVuSEp6SzZXRFU2Vnh2MXpKVjhMaWh0SUhSVmZ0U2ZIRklreVkwQk1CQ05WCnNNV0ZaQWo5M2l1YUU4eWhhM0lYSXdLQmdIaFMyVWhDMytVbFZITVdnVjdsK0NDY0tXRDJFdEUxVmoyK0JwMEUKM0FoTmwvWThEeG1nc015TStiWkwvSkFyNGNRNllZV3FBaEpJUTQxZEF2Und1ZmwyY3BRZmtOb0dxc283RWR3NgpSUmZBNE9OM3ZTSDhwL2syWG0zR0FENXZkc1VOTldBQ2J4b2hWb1NOS1VpR0dPRlE5U1psckkvakp1Qm9NQmVGCi9NbG5Bb0dBRk9nT3BhNTFTZVRxdUJhSXMzd3ZGaEVpbitiTE5meXNnWGVjMkdFQllsV1dYaThkL1p1Z3VtV0oKMXFaMVo0ODNlTUlQUU5RK2JVa1QxcVJoNlQ1a1pGV3lFMVVJWTR3RXRuM2ZDckhyQU5iZlhhQXIyOHVsUEEyQQpuYjlSZTRMcFpmVHZSVE02bVJJNGsrNHRLeGljL2FqR1QwNEFFekR6ckdJT25VVUVwVXM9Ci0tLS0tRU5EIFJTQSBQUklWQVRFIEtFWS0tLS0tCg==
"""

kubeconfig_unsupported_yaml = """
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUREekNDQWZlZ0F3SUJBZ0lVSDh2MmtKZDE0TEs4VWIrM1RmUGVUY21pMWNrd0RRWUpLb1pJaHZjTkFRRUwKQlFBd0Z6RVZNQk1HQTFVRUF3d01NVEF1TVRVeUxqRTRNeTR4TUI0WERUSXpNRFF3TkRBMU1Ua3lOVm9YRFRNegpNRFF3TVRBMU1Ua3lOVm93RnpFVk1CTUdBMVVFQXd3TU1UQXVNVFV5TGpFNE15NHhNSUlCSWpBTkJna3Foa2lHCjl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUF4RWkwVFhldmJYNFNvZ2VsRW16T0NQU2tYNHloOURCVGd6WFEKQkdJQTF4TDFwZ09mRkNMNzZYSlROSU4rYUNPT1BoVGp6dXoyR3dpR05pMHVBdnZyUGVrN0p0cEliUjg4YjRSQQpZUTRtMTllMU5zVjdwZ2pHL0JEQzVza1dycVpoZTR5ZTZoOXI2OXpKb1l5NEE4eFZLb1MvdElBZkdSejZvaS9uCndpY0ZzKzQyc29icm92MFdyUm5KbFV4eisyVHB2TFA1TW40eUExZHpGV0RLMTVCemVHa1YyYTVDeHBqcFBBTE4KVzUwVWlvSittbHBmTmwvYzZKWmFaZDR4S1NxclppU2dCY3BOQlhvWjJYVHpDOVNJTFF5RGZpZUpVNWxOcEIwSgpvSUphT0UvOTNseGp1bUdsSlRLSS9ucmpYM241UDFyaFFlWTNxV2p5S21ZNlFucjRqUUlEQVFBQm8xTXdVVEFkCkJnTlZIUTRFRmdRVU0yVTBMSTZtcGFaOTVkTnlIRGs1ZlZCck5ISXdId1lEVlIwakJCZ3dGb0FVTTJVMExJNm0KcGFaOTVkTnlIRGs1ZlZCck5ISXdEd1lEVlIwVEFRSC9CQVV3QXdFQi96QU5CZ2txaGtpRzl3MEJBUXNGQUFPQwpBUUVBZzZITWk4eTQrSENrOCtlb1FuamlmOHd4MytHVDZFNk02SWdRWWRvSFJjYXNYZ0JWLzd6OVRHQnpNeG1aCmdrL0Fnc08yQitLUFh3NmdQZU1GL1JLMjhGNlovK0FjYWMzdUtjT1N1WUJiL2lRKzI1cU9BazZaTStoSTVxMWQKUm1uVzBIQmpzNmg1bVlDODJrSVcrWStEYWN5bUx3OTF3S2ptTXlvMnh4OTBRb0IvWnBSVUxiNjVvWmlkcHZEawpOMStleFg4QmhIeE85S0lhMFFvcThVWFdLTjN4anZRb1pVanFieXY1VWFvcjBwbWpKT1NLKzJLMllRSk9FbUxaCkFDdEtzUDNpaU1UTlRXYUpxVjdWUVZaL3dRUVdsQ1h3VFp3WGlicXk0Z0kwb3JrcVNha0gzVFZMblVrRlFKU24KUi8waU1RRVFzQW5kajZBcVhlQml3ZG5aSGc9PQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0tCg==  # noqa: E501
    server: https://10.5.1.180:16443
  name: k8s-cluster
contexts:
- context:
    cluster: k8s-cluster
    user: admin
  name: k8s
current-context: k8s
kind: Config
preferences: {}
users:
- name: admin
  user:
    username: admin
    password: fake-password
"""


@pytest.fixture
def juju():
    yield Mock()


@pytest.fixture
def status():
    return jubilant.statustypes.Status._from_dict(
        {
            "model": {
                "name": "test-model",
                "controller": "test-controller",
                "cloud": "test-cloud",
                "region": "test-region",
                "version": "9723",
                "type": "k8s",
                "model_status": {},
            },
            "machines": {},
            "applications": {},
        }
    )


@pytest.fixture
def jhelper(juju, status) -> jujulib.JujuHelper:
    jhelper = jujulib.JujuHelper.__new__(jujulib.JujuHelper)
    jhelper.controller = "test"
    jhelper._juju = juju
    juju.status.return_value = status
    jhelper.models = Mock(
        return_value=[
            {
                "short-name": "test-model",
                "name": "admin/test-model",
                "model-uuid": "1234",
            }
        ]
    )
    return jhelper


def test_init_with_none():
    with pytest.raises(ValueError):
        jujulib.JujuHelper(None)


def test_cli_json_success(jhelper):
    jhelper._juju.cli.return_value = json.dumps({"app": "bar"})
    result = jhelper.cli("status")
    assert result == {"app": "bar"}


def test_cli_json_decode_error(jhelper):
    jhelper._juju.cli.return_value = "{bad json}"
    with pytest.raises(jujulib.CmdFailedException):
        jhelper.cli("status")


def test_get_model_found(jhelper):
    assert jhelper.get_model("test-model")


def test_get_model_not_found(jhelper):
    with pytest.raises(jujulib.ModelNotFoundException):
        jhelper.get_model("nope")


def test_model_exists_true(jhelper):
    assert jhelper.model_exists("test-model")


def test_model_exists_false(jhelper):
    assert not jhelper.model_exists("nope")


def test_get_model_name_with_owner(jhelper):
    assert jhelper.get_model_name_with_owner("test-model") == "admin/test-model"


def test_get_machines(jhelper, status):
    assert jhelper.get_machines("test-model") == status.machines


def test_get_application_found(jhelper, status):
    status.apps["app"] = Mock()
    assert jhelper.get_application("app", "test-model") == status.apps["app"]


def test_get_application_not_found(jhelper):
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper.get_application("app", "test-model")


def test_get_application_names(jhelper, status):
    status.apps.update({"app1": 1, "app2": 1})
    names = jhelper.get_application_names("test-model")
    assert names == ["app1", "app2"]


def test_validate_unit_valid(jhelper):
    jhelper._validate_unit("app/0")


def test_validate_unit_invalid(jhelper):
    with pytest.raises(ValueError):
        jhelper._validate_unit("badunit")


def test_add_unit_single(jhelper):
    jhelper.get_application = Mock(
        side_effect=[
            Mock(units={"app/0": Mock(machine="0")}),
            Mock(units={"app/0": Mock(machine="0"), "app/1": Mock(machine="1")}),
        ]
    )
    result = jhelper.add_unit("test-model", "app", "1")
    assert "app/1" in result


def test_remove_unit(jhelper, juju):
    jhelper.remove_unit("app", "app/0", "test-model")
    juju.remove_unit.assert_called()


def test_run_cmd_on_machine_unit_payload_success(jhelper, juju):
    juju.exec = Mock(return_value=Mock(success=True, results={"result": "ok"}))

    result = jhelper.run_cmd_on_machine_unit_payload("app/0", "test-model", "ls")
    assert result.results["result"] == "ok"


def test_run_action_success(jhelper, juju):
    juju.run = Mock(return_value=Mock(success=True, results={"app": "bar"}))

    result = jhelper.run_action("app/0", "test-model", "do-something")
    assert result["app"] == "bar"


def test_run_action_failure(jhelper, juju):
    juju.run = MagicMock(return_value=Mock(success=False, results={"app": "bar"}))

    with pytest.raises(jujulib.ActionFailedException):
        jhelper.run_action("app/0", "test-model", "do-something")


def test_run_cmd_on_unit_payload_success(jhelper, juju):
    juju._cli = MagicMock(
        return_value=(json.dumps({"app/0": {"results": {"out": "ok"}}}), "")
    )
    result = jhelper.run_cmd_on_unit_payload("app/0", "test-model", "ls", "container")
    assert result["out"] == "ok"


def test_run_cmd_on_unit_payload_cli_error(jhelper, juju):
    juju._cli.side_effect = jubilant.CLIError(
        1, "ls container", json.dumps({"app/0": {"results": {"err": "fail"}}})
    )
    result = jhelper.run_cmd_on_unit_payload("app/0", "test-model", "ls", "container")
    assert result["err"] == "fail"


def test_set_model_config(jhelper, juju):
    jhelper.set_model_config("test-model", {"app": "bar"})
    juju.model_config.assert_called()


def test_remove_application(jhelper, juju):
    jhelper.remove_application("app", model="test-model")
    juju.remove_application.assert_called()


def test_add_machine(jhelper, juju):
    juju._cli.return_value = ("", "machine-1")
    result = jhelper.add_machine("name", "test-model")
    assert result == "machine-1"


def test_charm_refresh(jhelper, juju):
    jhelper.charm_refresh("app", "test-model")
    juju.refresh.assert_called()


def test_get_spaces(jhelper):
    juju_mock = MagicMock()
    juju_mock.cli = MagicMock(return_value=json.dumps({"spaces": [{"name": "space1"}]}))

    class DummyContext:
        def __enter__(self):
            return juju_mock

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    jhelper._model = MagicMock(return_value=DummyContext())
    result = jhelper.get_spaces("test-model")
    assert result == [{"name": "space1"}]


def test_add_space_success(jhelper, juju):
    jhelper.add_space("test-model", "space", ["10.0.0.0/24"])
    juju.cli.assert_called()


def test_add_space_fail(jhelper, juju):
    juju.cli.side_effect = jubilant.CLIError(1, "add-space space 10.0.0.0/24", "fail")

    with pytest.raises(jujulib.JujuException):
        jhelper.add_space("test-model", "space", ["10.0.0.0/24"])


def test_get_space_networks_success(jhelper, juju):
    juju.cli.return_value = json.dumps(
        {"space": {"subnets": [{"cidr": "10.0.0.0/24"}]}}
    )

    result = jhelper.get_space_networks("test-model", "space")
    import ipaddress

    assert result == [ipaddress.ip_network("10.0.0.0/24")]


def test_get_space_networks_not_found(jhelper, juju):
    juju.cli.side_effect = jubilant.CLIError(1, "get-space space", stderr="not found")
    with pytest.raises(jujulib.JujuException):
        jhelper.get_space_networks("test-model", "space")


def test_get_space_networks_invalid_cidr(jhelper, juju):
    juju.cli.return_value = json.dumps({"space": {"subnets": [{"cidr": "badcidr"}]}})

    with pytest.raises(jujulib.JujuException):
        jhelper.get_space_networks("test-model", "space")


def test_remove_saas_success(jhelper, juju):
    jhelper.remove_saas("test-model", "saas1")
    juju.cli.assert_called()


def test_remove_saas_fail(jhelper, juju):
    # Patch jubilant.CLIError for this test
    juju.cli.side_effect = jubilant.CLIError(1, "remove-saas", "fail")
    with pytest.raises(jujulib.JujuException):
        jhelper.remove_saas("test-model", "saas1")


def test_destroy_model_found(jhelper, juju):
    jhelper.destroy_model("test-model")
    juju.destroy_model.assert_called_with(
        "admin/test-model", destroy_storage=False, force=False
    )


def test_destroy_model_not_found(jhelper, juju):
    jhelper.destroy_model("model")
    juju.destroy_model.assert_not_called()


def test_integrate_success(jhelper, juju, status):
    status.apps.update({"foo": 1, "bar": 1})
    jhelper.integrate("test-model", "foo", "bar", "rel")
    juju.integrate.assert_called_with("foo:rel", "bar:rel")


def test_integrate_missing_requirer(jhelper, status):
    status.apps["foo"] = 1
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper.integrate("test-model", "foo", "bar", "rel")


def test_integrate_missing_provider(jhelper, status):
    status.apps["bar"] = 1
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper.integrate("test-model", "foo", "bar", "rel")


def test_are_integrated_true(jhelper):
    app = Mock()
    rel = Mock()
    rel.related_app = "bar"
    app.relations = {"rel": [rel]}
    jhelper.get_application = Mock(return_value=app)
    assert jhelper.are_integrated("model", "foo", "bar", "rel") is True


def test_are_integrated_false_no_relations(jhelper):
    app = Mock()
    app.relations = {"rel": []}
    jhelper.get_application = Mock(return_value=app)
    assert jhelper.are_integrated("model", "foo", "bar", "rel") is False


def test_are_integrated_false_no_relation_key(jhelper):
    app = Mock()
    app.relations = {}
    jhelper.get_application = Mock(return_value=app)
    assert jhelper.are_integrated("model", "foo", "bar", "rel") is False


def test_are_integrated_false_wrong_related_app(jhelper):
    app = Mock()
    rel = Mock()
    rel.related_app = "baz"
    app.relations = {"rel": [rel]}
    jhelper.get_application = Mock(return_value=app)
    assert jhelper.are_integrated("model", "foo", "bar", "rel") is False


def test_get_model_status_success(jhelper, status):
    assert jhelper.get_model_status("test-model") == status


def test_get_model_status_not_found(jhelper, juju):
    juju.status.side_effect = jubilant.CLIError(1, "status", stderr="not found")
    with pytest.raises(jujulib.ModelNotFoundException):
        jhelper.get_model_status("test-model")


def test_get_model_status_other_error(jhelper, juju):
    juju.status.side_effect = jubilant.CLIError(1, "status", stderr="other error")
    with pytest.raises(jujulib.JujuException):
        jhelper.get_model_status("test-model")


def test_get_machine_interfaces_success(jhelper, status):
    machine = Mock(network_interfaces=1)
    status.machines.update({"0": machine})
    assert jhelper.get_machine_interfaces("test-model", "0") == 1


def test_get_machine_interfaces_not_found(jhelper):
    with pytest.raises(jujulib.MachineNotFoundException):
        jhelper.get_machine_interfaces("test-model", "1")


def test_deploy_simple(jhelper, juju):
    jhelper.deploy("app", "charm", "test-model")
    juju.deploy.assert_called_with(
        "charm",
        app="app",
        channel=None,
        revision=None,
        config=None,
        num_units=1,
        base="ubuntu@24.04",
        to=None,
    )


def test_deploy_all_args(jhelper, juju):
    jhelper.deploy(
        "app",
        "charm",
        "test-model",
        num_units=2,
        channel="edge",
        revision=5,
        to=["0"],
        config={"foo": "bar"},
        base="ubuntu@22.04",
    )
    juju.deploy.assert_called_with(
        "charm",
        app="app",
        channel="edge",
        revision=5,
        config={"foo": "bar"},
        num_units=2,
        base="ubuntu@22.04",
        to=["0"],
    )


def test_get_unit_success(jhelper, status):
    unit = Mock()
    status.apps["app"] = Mock(units={"app/0": unit})
    assert jhelper.get_unit("app/0", "test-model") is unit


def test_get_unit_app_not_found(jhelper, status):
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper.get_unit("app/0", "test-model")


def test_get_unit_unit_not_found(jhelper, status):
    status.apps["app"] = Mock(units={})
    with pytest.raises(jujulib.UnitNotFoundException):
        jhelper.get_unit("app/0", "test-model")


def test_get_unit_from_machine_success(jhelper, status):
    unit = Mock(machine="1")
    status.apps["app"] = Mock(units={"app/0": unit})
    assert jhelper.get_unit_from_machine("app", "1", "test-model") == "app/0"


def test_get_unit_from_machine_app_not_found(jhelper, status):
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper.get_unit_from_machine("app", "1", "test-model")


def test_get_unit_from_machine_unit_not_found(jhelper, status):
    status.apps["app"] = Mock(units={"app/0": Mock(machine="2")})
    with pytest.raises(jujulib.UnitNotFoundException):
        jhelper.get_unit_from_machine("app", "1", "test-model")


def test__get_leader_unit_success(jhelper, status):
    leader_unit = Mock(leader=True)
    non_leader_unit = Mock(leader=False)
    status.apps["app"] = Mock(units={"app/0": non_leader_unit, "app/1": leader_unit})
    name, unit = jhelper._get_leader_unit("app", "test-model")
    assert name == "app/1"
    assert unit is leader_unit


def test__get_leader_unit_no_leader(jhelper, status):
    status.apps["app"] = Mock(units={"app/0": Mock(leader=False)})
    with pytest.raises(jujulib.LeaderNotFoundException):
        jhelper._get_leader_unit("app", "test-model")


def test__get_leader_unit_app_not_found(jhelper, status):
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper._get_leader_unit("app", "test-model")


def test_grant_secret_success(jhelper, juju):
    jhelper.grant_secret("test-model", "secret-id", "app")
    juju.cli.assert_called()


def test_grant_secret_fail(jhelper, juju):
    juju.cli.side_effect = jubilant.CLIError(1, "grant-secret", "fail")
    with pytest.raises(jujulib.JujuException):
        jhelper.grant_secret("test-model", "secret-id", "app")


def test_get_secret_success(jhelper, juju):
    juju.cli.return_value = json.dumps(
        {"43434kj": {"content": {"Data": "secret data"}}}
    )
    assert jhelper.get_secret("test-model", "secret-id") == "secret data"
    juju.cli.assert_called()


def test_get_secret_fail(jhelper, juju):
    juju.cli.side_effect = jubilant.CLIError(1, "get-secret", stderr="fail")
    with pytest.raises(jujulib.JujuException):
        jhelper.get_secret("test-model", "secret-id")


def test_get_secret_not_found(jhelper, juju):
    juju.cli.side_effect = jubilant.CLIError(1, "get-secret", stderr="not found")
    with pytest.raises(jujulib.JujuSecretNotFound):
        jhelper.get_secret("test-model", "secret-id")


def test_get_app_config_success(jhelper, juju):
    test_config = {"key": "value"}
    juju.config.return_value = test_config
    config = jhelper.get_app_config("test-app", "test-model")
    assert test_config == config
    juju.config.assert_called()


def test_get_app_config_not_found(jhelper, juju):
    juju.config.side_effect = jubilant.CLIError(1, "config", stderr="not found")
    with pytest.raises(jujulib.ApplicationNotFoundException):
        jhelper.get_app_config("test-app", "test-model")


def test_get_app_config_fail(jhelper, juju):
    juju.config.side_effect = jubilant.CLIError(1, "config", stderr="fail")
    with pytest.raises(jujulib.JujuException):
        jhelper.get_secret("test-app", "test-model")


def test_jhelper_add_k8s_cloud(jhelper: jujulib.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_yaml)
    jhelper.add_k8s_cloud("k8s", "k8s-creds", kubeconfig)


def test_jhelper_add_k8s_cloud_with_client_certificate(jhelper: jujulib.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_clientcertificate_yaml)
    jhelper.add_k8s_cloud("k8s", "k8s-creds", kubeconfig)


def test_jhelper_add_k8s_cloud_unsupported_kubeconfig(jhelper: jujulib.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_unsupported_yaml)
    with pytest.raises(
        jujulib.UnsupportedKubeconfigException,
        match=(
            "Unsupported user credentials, only OAuth token and ClientCertificate are "
            "supported"
        ),
    ):
        jhelper.add_k8s_cloud("k8s", "k8s-creds", kubeconfig)


def test_get_available_charm_revision(jhelper: jujulib.JujuHelper, juju):
    cmd_out = {
        "channels": {
            "legacy": {
                "edge": [
                    {
                        "revision": 121,
                        "bases": [{"name": "ubuntu", "channel": "24.04"}],
                    }
                ]
            }
        }
    }
    juju.cli.return_value = json.dumps(cmd_out)
    revno = jhelper.get_available_charm_revision("k8s", "legacy/edge")
    assert revno == 121


class TestJujuStepHelper:
    def test_normalise_channel(self):
        jsh = jujulib.JujuStepHelper()
        assert jsh.normalise_channel("2023.2/edge") == "2023.2/edge"
        assert jsh.normalise_channel("edge") == "latest/edge"

    def test_channel_update_needed(self):
        jsh = jujulib.JujuStepHelper()
        assert jsh.channel_update_needed("2023.1/stable", "2023.2/stable")
        assert jsh.channel_update_needed("2023.1/stable", "2023.1/edge")
        assert jsh.channel_update_needed("latest/stable", "latest/edge")
        assert not jsh.channel_update_needed("2023.1/stable", "2023.1/stable")
        assert not jsh.channel_update_needed("2023.2/stable", "2023.1/stable")
        assert not jsh.channel_update_needed("latest/stable", "latest/stable")
        assert not jsh.channel_update_needed("foo/stable", "ba/stable")


class TestJujuActionHelper:
    def test_get_unit(self):
        mock_client = Mock()
        mock_client.cluster.get_node_info.return_value = {"machineid": "fakeid"}
        jhelper = Mock()

        jujulib.JujuActionHelper.get_unit(
            mock_client,
            jhelper,
            "test-model",
            "fake-node",
            "fake-app",
        )
        mock_client.cluster.get_node_info.assert_called_once_with("fake-node")
        jhelper.get_unit_from_machine.assert_called_once_with(
            "fake-app",
            "fakeid",
            "test-model",
        )

    @patch("sunbeam.core.juju.JujuActionHelper.get_unit")
    def test_run_action(self, mock_get_unit):
        mock_client = Mock()
        mock_jhelper = Mock()
        jujulib.JujuActionHelper.run_action(
            mock_client,
            mock_jhelper,
            "fake-model",
            "fake-node",
            "fake-app",
            "fake-action",
            {"p1": "v1", "p2": "v2"},
        )
        mock_get_unit.assert_called_once_with(
            mock_client,
            mock_jhelper,
            "fake-model",
            "fake-node",
            "fake-app",
        )

    @patch("sunbeam.core.juju.JujuActionHelper.get_unit")
    def test_run_action_unit_not_found_exception(self, mock_get_unit):
        mock_client = Mock()
        mock_jhelper = Mock()
        mock_get_unit.side_effect = jujulib.UnitNotFoundException
        with pytest.raises(jujulib.UnitNotFoundException):
            jujulib.JujuActionHelper.run_action(
                mock_client,
                mock_jhelper,
                "fake-model",
                "fake-node",
                "fake-app",
                "fake-action",
                {"p1": "v1", "p2": "v2"},
            )

    @patch("sunbeam.core.juju.JujuActionHelper.get_unit")
    def test_run_action_failed_exception(self, mock_get_unit):
        mock_client = Mock()
        mock_jhelper = Mock()
        mock_get_unit.side_effect = jujulib.ActionFailedException(Mock())
        with pytest.raises(jujulib.ActionFailedException):
            jujulib.JujuActionHelper.run_action(
                mock_client,
                mock_jhelper,
                "fake-model",
                "fake-node",
                "fake-app",
                "fake-action",
                {"p1": "v1", "p2": "v2"},
            )


def test_wait_until_desired_status_timeout(jhelper: jujulib.JujuHelper, juju):
    """Check wait_until_desired_status_for_apps behavior with nullable arguments."""
    juju.wait.side_effect = TimeoutError

    with pytest.raises(TimeoutError):
        jhelper.wait_until_desired_status("test-model", ["app1"])

    juju.wait.assert_called_once()


@pytest.mark.parametrize(
    "application_status, unit_list, expected_status, expected_agent_status, expected_workload_status_message, expected_result",
    [
        # Test case where all conditions are met
        (
            Mock(
                units={
                    "app1/0": Mock(
                        workload_status=Mock(current="active"),
                        juju_status=Mock(current="idle"),
                    ),
                    "app1/1": Mock(
                        workload_status=Mock(current="active"),
                        juju_status=Mock(current="idle"),
                    ),
                },
                subordinate_to=[],
                app_status=None,
                scale=2,
            ),
            [],
            {"active"},
            {"idle"},
            None,
            True,
        ),
        # Test case where workload status does not match
        (
            Mock(
                units={
                    "app1/0": Mock(
                        workload_status=Mock(current="blocked"),
                        juju_status=Mock(current="idle"),
                    ),
                },
                subordinate_to=[],
                app_status=None,
                scale=1,
            ),
            [],
            {"active"},
            {"idle"},
            None,
            False,
        ),
        # Test case where agent status does not match
        (
            Mock(
                units={
                    "app1/0": Mock(
                        workload_status=Mock(current="active"),
                        juju_status=Mock(status="executing"),
                    ),
                },
                subordinate_to=[],
                app_status=None,
                scale=1,
            ),
            [],
            {"active"},
            {"idle"},
            None,
            False,
        ),
        # Test case where workload status message does not match
        (
            Mock(
                units={
                    "app1/0": Mock(
                        workload_status=Mock(current="active", message="Error"),
                        juju_status=Mock(current="idle"),
                    ),
                },
                subordinate_to=[],
                app_status=None,
                scale=1,
            ),
            [],
            {"active"},
            {"idle"},
            {"Ready"},
            False,
        ),
        (
            Mock(
                units={},
                subordinate_to=["app0"],
                app_status=Mock(current="active"),
                scale=0,
            ),
            [],
            {"active"},
            None,
            None,
            True,
        ),
        # Test case where unit list is specified
        (
            Mock(
                units={
                    "app1/0": Mock(
                        workload_status=Mock(current="active"),
                        juju_status=Mock(current="idle"),
                    ),
                    "app1/1": Mock(
                        workload_status=Mock(current="blocked"),
                        juju_status=Mock(current="idle"),
                    ),
                },
                subordinate_to=[],
                app_status=None,
                scale=2,
            ),
            ["app1/0"],
            {"active"},
            {"idle"},
            None,
            True,
        ),
    ],
)
def test_is_desired_status_achieved(
    application_status,
    unit_list,
    expected_status,
    expected_agent_status,
    expected_workload_status_message,
    expected_result,
):
    result = jujulib.JujuHelper._is_desired_status_achieved(
        application_status,
        unit_list,
        expected_status,
        expected_agent_status,
        expected_workload_status_message,
    )
    assert result == expected_result
