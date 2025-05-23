# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
import yaml
from juju.application import Application
from juju.client import connector as juju_connector
from juju.model import Model
from juju.unit import Unit
from websockets import ConnectionClosedError

import sunbeam.core.juju as juju

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


def _unit_getter(u, m):
    mock = Mock()
    mock.name = u
    return mock


@pytest.fixture
def applications() -> dict[str, Application]:
    mock = MagicMock()
    k8s_unit_mock = AsyncMock(
        entity_id="k8s/0",
        agent_status="idle",
        workload_status="active",
    )
    k8s_unit_mock.is_leader_from_status.return_value = True

    mk8s_unit_mock = AsyncMock(
        entity_id="mk8s/0",
        agent_status="idle",
        workload_status="active",
    )
    mk8s_unit_mock.is_leader_from_status.return_value = False

    app_dict = {
        "k8s": AsyncMock(status="active", units=[k8s_unit_mock]),
        "mk8s": AsyncMock(status="unknown", units=[mk8s_unit_mock]),
    }
    mock.get.side_effect = app_dict.get
    mock.__getitem__.side_effect = app_dict.__getitem__
    return mock


@pytest.fixture
def units() -> dict[str, Unit]:
    mock = MagicMock()
    k8s_0_unit_mock = AsyncMock(
        entity_id="k8s/0",
        agent_status="idle",
        workload_status="active",
    )
    k8s_0_unit_mock.run_action.return_value = AsyncMock(
        _status="completed",
        results={"exit_code": 0},
    )

    k8s_1_unit_mock = AsyncMock(
        entity_id="k8s/1",
        agent_status="unknown",
        workload_status="unknown",
    )
    k8s_1_unit_mock.run_action.return_value = AsyncMock(
        _status="failed",
        results={"exit_code": 1},
    )

    unit_dict = {
        "k8s/0": k8s_0_unit_mock,
        "k8s/1": k8s_1_unit_mock,
    }
    mock.get.side_effect = unit_dict.get
    mock.__getitem__.side_effect = unit_dict.__getitem__
    return mock


@pytest.fixture
def model(applications, units) -> Model:
    model = AsyncMock()
    model.__aenter__.return_value = model
    model.name = "control-plane"
    model.units = units
    model.applications = applications
    model.all_units_idle = Mock()
    model.info = Mock()

    def test_condition(condition, timeout):
        """False condition raises a timeout"""
        result = condition()
        model.block_until.result = result
        if not result:
            raise asyncio.TimeoutError(f"Timed out after {timeout} seconds")
        return result

    model.block_until.side_effect = test_condition

    model.get_action_output.return_value = "action failed..."

    return model


@pytest.fixture
def jhelper_base(tmp_path: Path) -> juju.JujuHelper:
    jhelper = juju.JujuHelper.__new__(juju.JujuHelper)
    jhelper.data_location = tmp_path
    jhelper.controller = AsyncMock()  # type: ignore
    jhelper.model_connectors = []
    return jhelper


@pytest.fixture
def jhelper_404(jhelper_base: juju.JujuHelper):
    # pyright: reportGeneralTypeIssues=false
    jhelper_base.controller.get_model.side_effect = Exception("HTTP 400")
    yield jhelper_base
    jhelper_base.controller.get_model.side_effect = None


@pytest.fixture
def jhelper_unknown_error(jhelper_base: juju.JujuHelper):
    # pyright: reportGeneralTypeIssues=false
    jhelper_base.controller.get_model.side_effect = Exception("Unknown error")
    yield jhelper_base
    jhelper_base.controller.get_model.side_effect = None


@pytest.fixture
def jhelper(mocker, jhelper_base: juju.JujuHelper, model):
    jhelper_base.controller.get_model.return_value = model
    yield jhelper_base


@pytest.mark.asyncio
async def test_jhelper_get_clouds(jhelper: juju.JujuHelper):
    await jhelper.get_clouds()
    jhelper.controller.clouds.assert_called_once()


@pytest.mark.asyncio
async def test_jhelper_get_model(jhelper: juju.JujuHelper):
    await jhelper.get_model("control-plane")
    jhelper.controller.get_model.assert_called_with("control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_model_missing(
    jhelper_404: juju.JujuHelper,
):
    with pytest.raises(juju.ModelNotFoundException, match="Model 'missing' not found"):
        await jhelper_404.get_model("missing")


@pytest.mark.asyncio
async def test_jhelper_get_model_unknown_error(
    jhelper_unknown_error: juju.JujuHelper,
):
    with pytest.raises(Exception, match="Unknown error"):
        await jhelper_unknown_error.get_model("control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_model_status_full(jhelper: juju.JujuHelper, model):
    model.get_status.return_value = Mock(to_json=Mock(return_value="{}"))
    await jhelper.get_model_status_full("control-plane")
    jhelper.controller.get_model.assert_called_with("control-plane")
    model.get_status.assert_called_once()


@pytest.mark.asyncio
async def test_jhelper_get_model_status_full_model_missing(
    jhelper_404: juju.JujuHelper, model
):
    with pytest.raises(juju.ModelNotFoundException, match="Model 'missing' not found"):
        await jhelper_404.get_model_status_full("missing")
        model.get_status.assert_not_called()


@pytest.mark.asyncio
async def test_jhelper_get_model_name_with_owner(jhelper: juju.JujuHelper, model):
    await jhelper.get_model_name_with_owner("control-plane")
    jhelper.controller.get_model.assert_called_with("control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_model_name_with_owner_model_missing(
    jhelper_404: juju.JujuHelper, model
):
    with pytest.raises(juju.ModelNotFoundException, match="Model 'missing' not found"):
        await jhelper_404.get_model_name_with_owner("missing")
        jhelper_404.controller.get_model.assert_called_with("missing")


@pytest.mark.asyncio
async def test_jhelper_get_unit(jhelper: juju.JujuHelper, model, units):
    await jhelper.get_unit("k8s/0", model)
    units.get.assert_called_with("k8s/0")


@pytest.mark.asyncio
async def test_jhelper_get_unit_missing(jhelper: juju.JujuHelper, model):
    name = "mysql/0"
    with pytest.raises(
        juju.UnitNotFoundException,
        match=f"Unit {name!r} is missing from model {model.name!r}",
    ):
        await jhelper.get_unit(name, model)


@pytest.mark.asyncio
async def test_jhelper_get_unit_invalid_name(jhelper: juju.JujuHelper, model):
    with pytest.raises(
        ValueError,
        match=(
            "Name 'k8s' has invalid format, "
            "should be a valid unit of format application/id"
        ),
    ):
        await jhelper.get_unit("k8s", model)


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = "k8s"
    unit = await jhelper.get_leader_unit(app, "control-plane")
    assert unit is not None
    applications.get.assert_called_with(app)


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit_missing_application(jhelper: juju.JujuHelper):
    model = "control-plane"
    app = "mysql"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application missing from model: {model!r}",
    ):
        await jhelper.get_leader_unit(app, model)


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit_missing(jhelper: juju.JujuHelper):
    model = "control-plane"
    app = "mk8s"
    with pytest.raises(
        juju.LeaderNotFoundException,
        match=f"Leader for application {app!r} is missing from model {model!r}",
    ):
        await jhelper.get_leader_unit(app, model)


@pytest.mark.asyncio
async def test_jhelper_get_application(
    jhelper: juju.JujuHelper, model, applications: dict[str, Application]
):
    app = await jhelper.get_application("k8s", model)
    assert app is not None
    applications.get.assert_called_with("k8s")


@pytest.mark.asyncio
async def test_jhelper_get_application_missing(jhelper: juju.JujuHelper, model):
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application missing from model: {model.name!r}",
    ):
        await jhelper.get_application("mysql", model)


@pytest.mark.asyncio
async def test_jhelper_add_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = applications["k8s"]
    await jhelper.add_unit(app)
    applications["k8s"].add_unit.assert_called_with(1, None)


@pytest.mark.asyncio
async def test_jhelper_add_unit_to_machine(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = applications["k8s"]
    await jhelper.add_unit(app, machine="0")
    applications["k8s"].add_unit.assert_called_with(1, "0")


@pytest.mark.asyncio
async def test_jhelper_remove_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    await jhelper.remove_unit("k8s", "k8s/0", "control-plane")
    applications["k8s"].destroy_unit.assert_called_with("k8s/0")


@pytest.mark.asyncio
async def test_jhelper_remove_unit_missing_application(
    jhelper: juju.JujuHelper,
):
    name = "mysql"
    unit = "mysql/0"
    model = "control-plane"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application {name!r} is missing from model {model!r}",
    ):
        await jhelper.remove_unit(name, unit, model)


@pytest.mark.asyncio
async def test_jhelper_remove_unit_invalid_unit(
    jhelper: juju.JujuHelper,
):
    with pytest.raises(
        ValueError,
        match=(
            "Name 'k8s' has invalid format, "
            "should be a valid unit of format application/id"
        ),
    ):
        await jhelper.remove_unit("k8s", "k8s", "control-plane")


@pytest.mark.asyncio
async def test_jhelper_run_action(jhelper: juju.JujuHelper, units):
    unit = "k8s/0"
    action_name = "get-action"
    await jhelper.run_action(unit, "control-plane", action_name)
    units.get(unit).run_action.assert_called_once_with(action_name)


@pytest.mark.asyncio
async def test_jhelper_run_action_failed(jhelper: juju.JujuHelper):
    with pytest.raises(
        juju.ActionFailedException,
        match="action failed...",
    ):
        await jhelper.run_action("k8s/1", "control-plane", "get-action")


@pytest.mark.asyncio
async def test_jhelper_scp_from(jhelper: juju.JujuHelper, units):
    unit = "k8s/0"
    await jhelper.scp_from(unit, "control-plane", "source", "destination")
    units.get(unit).scp_from.assert_called_once_with("source", "destination")


@pytest.mark.asyncio
async def test_jhelper_add_k8s_cloud(jhelper: juju.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_yaml)
    await jhelper.add_k8s_cloud("k8s", "k8s-creds", kubeconfig)


@pytest.mark.asyncio
async def test_jhelper_add_k8s_cloud_with_client_certificate(jhelper: juju.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_clientcertificate_yaml)
    await jhelper.add_k8s_cloud("k8s", "k8s-creds", kubeconfig)


@pytest.mark.asyncio
async def test_jhelper_add_k8s_cloud_unsupported_kubeconfig(jhelper: juju.JujuHelper):
    kubeconfig = yaml.safe_load(kubeconfig_unsupported_yaml)
    with pytest.raises(
        juju.UnsupportedKubeconfigException,
        match=(
            "Unsupported user credentials, only OAuth token and ClientCertificate are "
            "supported"
        ),
    ):
        await jhelper.add_k8s_cloud("k8s", "k8s-creds", kubeconfig)


test_data_k8s = [
    ("wait_application_ready", "k8s", "application 'k8s'", [["blocked"]]),
]

test_data_custom_status = [
    ("wait_application_ready", "mk8s", ["unknown"]),
]

test_data_missing = [
    ("wait_application_ready", "mysql"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,error,args", test_data_k8s)
async def test_jhelper_wait_ready(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str, error: str, args
):
    with patch.object(jhelper, "get_unit", side_effect=_unit_getter):
        await getattr(jhelper, method)(entity, "control-plane")
    assert model.block_until.call_count == 1
    assert model.block_until.result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,error,args", test_data_k8s)
async def test_jhelper_wait_application_ready_timeout(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str, error: str, args
):
    with (
        pytest.raises(
            juju.TimeoutException,
            match=f"Timed out while waiting for {error} to be ready",
        ),
        patch.object(jhelper, "get_unit", side_effect=_unit_getter),
    ):
        await getattr(jhelper, method)(entity, "control-plane", *args)
    assert model.block_until.call_count == 1
    assert model.block_until.result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity,status", test_data_custom_status)
async def test_jhelper_wait_ready_custom_status(
    jhelper: juju.JujuHelper,
    model: Model,
    method: str,
    entity: str,
    status: list | dict,
):
    with patch.object(jhelper, "get_unit", side_effect=_unit_getter):
        await getattr(jhelper, method)(entity, "control-plane", accepted_status=status)
    assert model.block_until.call_count == 1
    assert model.block_until.result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,entity", test_data_missing)
async def test_jhelper_wait_ready_missing_application(
    jhelper: juju.JujuHelper, model: Model, method: str, entity: str
):
    await getattr(jhelper, method)(entity, "control-plane")
    assert model.block_until.call_count == 0


@pytest.mark.asyncio
async def test_jhelper_wait_until_active(mocker, jhelper: juju.JujuHelper, model):
    gather = AsyncMock()
    mocker.patch("asyncio.gather", gather)
    await jhelper.wait_until_active("control-plane")
    assert gather.call_count == 1


@pytest.mark.asyncio
async def test_jhelper_wait_until_active_unit_in_error_state(
    jhelper: juju.JujuHelper, model
):
    jhelper._wait_until_status_coroutine = AsyncMock(
        side_effect=juju.JujuWaitException("Unit is in error state")
    )

    model.applications = {"keystone": (), "nova": ()}
    with pytest.raises(
        juju.JujuWaitException,
        match="Unit is in error state",
    ):
        await jhelper.wait_until_active("control-plane")
    assert jhelper._wait_until_status_coroutine.call_count == 2


@pytest.mark.asyncio
async def test_jhelper_wait_until_active_timed_out(
    mocker, jhelper: juju.JujuHelper, model
):
    gather = AsyncMock()
    gather.side_effect = asyncio.TimeoutError("timed out...")
    mocker.patch("asyncio.gather", gather)

    jhelper._wait_until_status_coroutine = AsyncMock()

    model.applications = {"keystone": (), "nova": ()}
    with pytest.raises(
        juju.TimeoutException,
        match="Timed out while waiting for model",
    ):
        await jhelper.wait_until_active("control-plane")
    assert jhelper._wait_until_status_coroutine.call_count == 2


@pytest.mark.asyncio
async def test_get_available_charm_revision(jhelper: juju.JujuHelper, model):
    cmd_out = {"channel-map": {"legacy/edge": {"revision": {"version": "121"}}}}
    with patch.object(juju.juju_charmhub, "CharmHub") as p:
        charmhub = AsyncMock()
        charmhub.info.return_value = cmd_out
        p.return_value = charmhub
        revno = await jhelper.get_available_charm_revision(
            "openstack", "k8s", "legacy/edge"
        )
        assert revno == 121


class TestJujuStepHelper:
    def test_revision_update_needed(self, jhelper):
        jsh = juju.JujuStepHelper()
        jsh.jhelper = jhelper
        CHARMREV = {"nova-k8s": 31, "cinder-k8s": 51, "another-k8s": 70}

        def _get_available_charm_revision(model, charm_name, deployed_channel):
            return CHARMREV[charm_name]

        _status = {
            "applications": {
                "nova": {
                    "charm": "ch:amd64/jammy/nova-k8s-30",
                    "charm-channel": "2023.2/edge/gnuoy",
                },
                "cinder": {
                    "charm": "ch:amd64/jammy/cinder-k8s-50",
                    "charm-channel": "2023.2/edge",
                },
                "another": {
                    "charm": "ch:amd64/jammy/another-k8s-70",
                    "charm-channel": "edge",
                },
            }
        }
        jhelper.get_available_charm_revision = AsyncMock()
        jhelper.get_available_charm_revision.side_effect = _get_available_charm_revision
        assert jsh.revision_update_needed("cinder", "openstack", _status)
        assert not jsh.revision_update_needed("nova", "openstack", _status)
        assert not jsh.revision_update_needed("another", "openstack", _status)

    def test_normalise_channel(self):
        jsh = juju.JujuStepHelper()
        assert jsh.normalise_channel("2023.2/edge") == "2023.2/edge"
        assert jsh.normalise_channel("edge") == "latest/edge"

    def test_extract_charm_name(self):
        jsh = juju.JujuStepHelper()
        assert jsh._extract_charm_name("ch:amd64/jammy/cinder-k8s-50") == "cinder-k8s"

    def test_extract_charm_revision(self):
        jsh = juju.JujuStepHelper()
        assert jsh._extract_charm_revision("ch:amd64/jammy/cinder-k8s-50") == "50"

    def test_channel_update_needed(self):
        jsh = juju.JujuStepHelper()
        assert jsh.channel_update_needed("2023.1/stable", "2023.2/stable")
        assert jsh.channel_update_needed("2023.1/stable", "2023.1/edge")
        assert jsh.channel_update_needed("latest/stable", "latest/edge")
        assert not jsh.channel_update_needed("2023.1/stable", "2023.1/stable")
        assert not jsh.channel_update_needed("2023.2/stable", "2023.1/stable")
        assert not jsh.channel_update_needed("latest/stable", "latest/stable")
        assert not jsh.channel_update_needed("foo/stable", "ba/stable")


class TestJujuActionHelper:
    @patch("sunbeam.core.juju.run_sync")
    def test_get_unit(self, mock_run_sync):
        mock_client = Mock()
        mock_jhelper = Mock()
        mock_client.cluster.get_node_info.return_value = {"machineid": "fakeid"}
        side_effect = [Mock(), Mock()]
        mock_run_sync.side_effect = side_effect

        juju.JujuActionHelper.get_unit(
            mock_client,
            mock_jhelper,
            "fake-model",
            "fake-node",
            "fake-app",
        )
        mock_client.cluster.get_node_info.assert_called_once_with("fake-node")
        mock_jhelper.get_unit_from_machine.assert_called_once_with(
            "fake-app",
            "fakeid",
            side_effect[0],
        )
        mock_run_sync.assert_has_calls(
            [
                call(mock_jhelper.get_model.return_value),
                call(mock_jhelper.get_unit_from_machine.return_value),
            ]
        )

    @patch("sunbeam.core.juju.JujuActionHelper.get_unit")
    @patch("sunbeam.core.juju.run_sync")
    def test_run_action(self, mock_run_sync, mock_get_unit):
        mock_client = Mock()
        mock_jhelper = Mock()
        result = juju.JujuActionHelper.run_action(
            mock_client,
            mock_jhelper,
            "fake-model",
            "fake-node",
            "fake-app",
            "fake-action",
            {"p1": "v1", "p2": "v2"},
        )
        assert result == mock_run_sync.return_value
        mock_get_unit.assert_called_once_with(
            mock_client,
            mock_jhelper,
            "fake-model",
            "fake-node",
            "fake-app",
        )
        mock_run_sync.assert_called_once_with(mock_jhelper.run_action.return_value)

    @patch("sunbeam.core.juju.JujuActionHelper.get_unit")
    def test_run_action_unit_not_found_exception(self, mock_get_unit):
        mock_client = Mock()
        mock_jhelper = Mock()
        mock_get_unit.side_effect = juju.UnitNotFoundException
        with pytest.raises(juju.UnitNotFoundException):
            juju.JujuActionHelper.run_action(
                mock_client,
                mock_jhelper,
                "fake-model",
                "fake-node",
                "fake-app",
                "fake-action",
                {"p1": "v1", "p2": "v2"},
            )

    @patch("sunbeam.core.juju.JujuActionHelper.get_unit")
    @patch("sunbeam.core.juju.run_sync")
    def test_run_action_failed_exception(self, mock_run_sync, mock_get_unit):
        mock_client = Mock()
        mock_jhelper = Mock()
        mock_get_unit.side_effect = juju.ActionFailedException(Mock())
        with pytest.raises(juju.ActionFailedException):
            juju.JujuActionHelper.run_action(
                mock_client,
                mock_jhelper,
                "fake-model",
                "fake-node",
                "fake-app",
                "fake-action",
                {"p1": "v1", "p2": "v2"},
            )


@pytest.mark.asyncio
async def test_wait_until_desired_status_for_apps(jhelper: juju.JujuHelper):
    model = AsyncMock(spec=Model)
    model.__aenter__.return_value = model
    model.applications = {
        "app1": None,
        "app2": None,
    }


@pytest.fixture
def shared_updater():
    _SharedStatusUpdater_class = Mock()
    shared_updater = AsyncMock(spec=juju._SharedStatusUpdater)
    _SharedStatusUpdater_class.return_value = shared_updater
    with patch("sunbeam.core.juju._SharedStatusUpdater", _SharedStatusUpdater_class):
        yield shared_updater


@pytest.mark.asyncio
async def test_wait_until_desired_status_for_apps_with_units(
    jhelper: juju.JujuHelper, shared_updater: juju._SharedStatusUpdater
):
    _wait_until_status_coroutine = AsyncMock()
    with (
        patch.object(
            jhelper, "_wait_until_status_coroutine", _wait_until_status_coroutine
        ),
    ):
        await jhelper.wait_until_desired_status(
            "control-plane", ["app1"], units=["app1/2"], status=["blocked"]
        )
        assert _wait_until_status_coroutine.call_count == 1
        assert _wait_until_status_coroutine.call_args_list == [
            ((shared_updater, "app1", ["app1/2"], None, {"blocked"}, None, None),),
        ]


@pytest.mark.asyncio
async def test_wait_until_desired_status_invalid_queue(jhelper: juju.JujuHelper):
    queue: asyncio.Queue[str] = asyncio.Queue(1)

    with (
        patch.object(jhelper, "get_model", return_value=model),
    ):
        with pytest.raises(ValueError):
            await jhelper.wait_until_desired_status(
                "control-plane", ["app1", "app2"], queue=queue
            )


@pytest.mark.asyncio
async def test_wait_until_desired_status_timeout(
    jhelper: juju.JujuHelper, shared_updater: juju._SharedStatusUpdater
):
    """Check wait_until_desired_status_for_apps behavior with nullable arguments."""
    _wait_until_status_coroutine = AsyncMock()

    with (
        patch("asyncio.gather", AsyncMock(side_effect=asyncio.TimeoutError)),
        patch.object(
            jhelper, "_wait_until_status_coroutine", _wait_until_status_coroutine
        ),
    ):
        with pytest.raises(juju.TimeoutException):
            await jhelper.wait_until_desired_status("control-plane", ["app1"])

        assert _wait_until_status_coroutine.call_count == 1
        assert _wait_until_status_coroutine.call_args_list == [
            ((shared_updater, "app1", None, None, {"active"}, None, None),),
        ]


@pytest.mark.asyncio
async def test_wait_until_desired_status_task_exception(
    jhelper: juju.JujuHelper, shared_updater: juju._SharedStatusUpdater
):
    _wait_until_status_coroutine = AsyncMock(side_effect=ValueError)

    with (
        patch.object(
            jhelper, "_wait_until_status_coroutine", _wait_until_status_coroutine
        ),
    ):
        with pytest.raises(juju.JujuWaitException):
            await jhelper.wait_until_desired_status("control-plane", ["app1"])

        assert _wait_until_status_coroutine.call_count == 1
        assert _wait_until_status_coroutine.call_args_list == [
            ((shared_updater, "app1", None, None, {"active"}, None, None),),
        ]


async def async_gen(items):
    for item in items:
        yield item


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "queue,expected_queue,desired_status",
    [
        (None, None, True),
        (asyncio.Queue(1), "app1", True),
        (asyncio.Queue(1), "app1", False),
    ],
)
async def test_wait_until_status_coroutine(
    shared_updater: juju._SharedStatusUpdater, queue, expected_queue, desired_status
):
    shared_updater.tick_status.return_value = async_gen(
        [Mock(applications={"app1": Mock(status="active")})]
    )
    with patch.object(
        juju.JujuHelper,
        "_is_desired_status_achieved",
        Mock(return_value=desired_status),
    ):
        await juju.JujuHelper._wait_until_status_coroutine(
            shared_updater, "app1", None, queue, {"active"}, None
        )
    assert shared_updater.tick_status.call_count == 1
    if queue:
        if desired_status:
            assert queue.get_nowait() == expected_queue
        else:
            with pytest.raises(asyncio.QueueEmpty):
                queue.get_nowait()


@pytest.mark.asyncio
async def test_wait_until_status_coroutine_cancelled(
    shared_updater: juju._SharedStatusUpdater,
):
    async def _tick_status():
        yield await asyncio.sleep(10)

    shared_updater.tick_status.return_value = _tick_status()
    task = asyncio.create_task(
        juju.JujuHelper._wait_until_status_coroutine(
            shared_updater, "app1", None, None, {"active"}, None
        )
    )
    await asyncio.sleep(0.01)
    # cancelling the task should not raise an exception
    task.cancel()


@pytest.mark.asyncio
async def test_wait_until_status_coroutine_missing_app(
    shared_updater: juju._SharedStatusUpdater,
):
    status = AsyncMock(applications={})
    shared_updater.tick_status.return_value = async_gen([status])
    with pytest.raises(ValueError):
        await juju.JujuHelper._wait_until_status_coroutine(shared_updater, "app1")
    assert shared_updater.tick_status.call_count == 1


@pytest.mark.parametrize(
    "application_status, unit_list, expected_status, expected_agent_status, expected_workload_status_message, expected_result",
    [
        # Test case where all conditions are met
        (
            Mock(
                units={
                    "app1/0": Mock(
                        workload_status=Mock(status="active"),
                        agent_status=Mock(status="idle"),
                    ),
                    "app1/1": Mock(
                        workload_status=Mock(status="active"),
                        agent_status=Mock(status="idle"),
                    ),
                },
                subordinate_to=None,
                status=None,
                int_=2,
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
                        workload_status=Mock(status="blocked"),
                        agent_status=Mock(status="idle"),
                    ),
                },
                subordinate_to=None,
                status=None,
                int_=1,
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
                        workload_status=Mock(status="active"),
                        agent_status=Mock(status="executing"),
                    ),
                },
                subordinate_to=None,
                status=None,
                int_=1,
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
                        workload_status=Mock(status="active", info="Error"),
                        agent_status=Mock(status="idle"),
                    ),
                },
                subordinate_to=None,
                status=None,
                int_=1,
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
                subordinate_to="app0",
                status=Mock(status="active"),
                int_=None,
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
                        workload_status=Mock(status="active"),
                        agent_status=Mock(status="idle"),
                    ),
                    "app1/1": Mock(
                        workload_status=Mock(status="blocked"),
                        agent_status=Mock(status="idle"),
                    ),
                },
                subordinate_to=None,
                status=None,
                int_=2,
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
    result = juju.JujuHelper._is_desired_status_achieved(
        application_status,
        unit_list,
        expected_status,
        expected_agent_status,
        expected_workload_status_message,
    )
    assert result == expected_result


@pytest.mark.asyncio
async def test_model_ticker_runs_until_cancelled(jhelper: juju.JujuHelper):
    shared_updater = Mock()
    shared_updater.reconnect_model_and_notify_awaiters.return_value = asyncio.sleep(10)

    ticker_task = asyncio.create_task(jhelper._model_ticker(shared_updater))

    await asyncio.sleep(0.01)
    ticker_task.cancel()

    await ticker_task
    assert ticker_task.done()


@pytest.mark.asyncio
async def test_model_ticker_silences_exceptions(jhelper: juju.JujuHelper):
    shared_updater = AsyncMock()
    shared_updater.reconnect_model_and_notify_awaiters.side_effect = Exception(
        "Test exception"
    )
    ticker_task = asyncio.create_task(jhelper._model_ticker(shared_updater))
    await asyncio.sleep(0.01)

    ticker_task.cancel()
    assert shared_updater.reconnect_model_and_notify_awaiters.call_count > 0
    await ticker_task


@pytest.mark.asyncio
async def test_tick_status_yields_status(jhelper: juju.JujuHelper):
    status_mock = Mock()
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._model_impl = AsyncMock()
    shared_updater._model_impl.get_status.return_value = status_mock

    with patch.object(shared_updater, "is_connected", Mock(return_value=True)):

        async def unlock():
            await asyncio.sleep(0.01)
            async with shared_updater._condition:
                shared_updater._condition.notify_all()

        gen = shared_updater.tick_status()
        asyncio.create_task(unlock())
        result = await gen.__anext__()

    assert result == status_mock
    shared_updater._model_impl.get_status.assert_called_once()


@pytest.mark.asyncio
async def test_tick_status_retries_on_connection_closed_error(
    jhelper: juju.JujuHelper,
):
    status_mock = Mock()
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._model_impl = AsyncMock()
    shared_updater._model_impl.get_status.side_effect = [
        ConnectionClosedError("Test error", None),
        status_mock,
    ]

    with patch.object(shared_updater, "is_connected", Mock(return_value=True)):

        async def unlock():
            for i in range(2):
                await asyncio.sleep(0.01)
                async with shared_updater._condition:
                    shared_updater._condition.notify_all()

        gen = shared_updater.tick_status()
        asyncio.create_task(unlock())
        result = await gen.__anext__()

    assert result == status_mock
    assert shared_updater._model_impl.get_status.call_count == 2


@pytest.mark.asyncio
async def test_tick_status_handles_cancelled_error(
    jhelper: juju.JujuHelper,
):
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._model_impl = Mock()
    task = asyncio.create_task(asyncio.sleep(10))
    await asyncio.sleep(0.01)
    task.cancel()
    shared_updater._model_impl.get_status.return_value = task

    with patch.object(shared_updater, "is_connected", Mock(return_value=True)):

        async def unlock():
            for i in range(2):
                await asyncio.sleep(0.01)
                async with shared_updater._condition:
                    shared_updater._condition.notify_all()

        gen = shared_updater.tick_status()
        asyncio.create_task(unlock())
        await asyncio.sleep(0.01)
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    shared_updater._model_impl.get_status.assert_called_once()


@pytest.mark.asyncio
async def test_tick_status_skips_when_not_connected(
    jhelper: juju.JujuHelper,
):
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._model_impl = AsyncMock()
    shared_updater._model_impl.get_status.return_value = None

    with patch.object(shared_updater, "is_connected", Mock(side_effect=[False, True])):

        async def unlock():
            for i in range(2):
                await asyncio.sleep(0.01)
                async with shared_updater._condition:
                    shared_updater._condition.notify_all()

        gen = shared_updater.tick_status()
        asyncio.create_task(unlock())
        await asyncio.sleep(0.01)
        await gen.__anext__()

    shared_updater._model_impl.get_status.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_model_and_notify_awaiters_connected():
    jhelper = Mock()
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._condition = AsyncMock()
    shared_updater._model_impl = AsyncMock()
    with patch.object(shared_updater, "is_connected", Mock(return_value=True)):
        await shared_updater.reconnect_model_and_notify_awaiters()
    shared_updater._condition.notify_all.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_model_and_notify_awaiters_reconnect_controller():
    jhelper = AsyncMock()
    jhelper.controller.is_connected = Mock(return_value=False)
    jhelper.get_model.return_value = AsyncMock()
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._condition = AsyncMock()
    is_connected_mock = Mock(side_effect=[False, True])
    mocker = patch.object(shared_updater, "is_connected", is_connected_mock)
    mocker.start()
    await shared_updater.reconnect_model_and_notify_awaiters()
    mocker.stop()
    assert is_connected_mock.call_count == 2

    jhelper.reconnect.assert_called_once()
    shared_updater._condition.notify_all.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_model_and_notify_awaiters_inner_connection_gone():
    jhelper = AsyncMock()
    jhelper.controller.is_connected = Mock(return_value=True)
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._condition = AsyncMock()
    shared_updater._model_impl = AsyncMock()
    shared_updater._model_impl.is_connected = Mock(return_value=False)
    with patch.object(shared_updater, "is_connected", Mock(side_effect=[False, True])):
        await shared_updater.reconnect_model_and_notify_awaiters()
    shared_updater._model_impl.disconnect.assert_not_called()
    jhelper.get_model.assert_called_once_with("control-plane")
    shared_updater._condition.notify_all.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_model_and_notify_awaiters_inner_socket_gone():
    jhelper = AsyncMock()
    jhelper.controller.is_connected = Mock(return_value=True)
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._condition = AsyncMock()
    shared_updater._model_impl = AsyncMock()
    shared_updater._model_impl.is_connected = Mock(return_value=True)
    shared_updater._model_impl.connection = Mock(
        return_value=Mock(monitor=Mock(status="closed"))
    )
    disconnect_mock = AsyncMock()
    shared_updater._model_impl.disconnect = disconnect_mock
    with patch.object(shared_updater, "is_connected", Mock(side_effect=[False, True])):
        await shared_updater.reconnect_model_and_notify_awaiters()
    disconnect_mock.assert_called_once()
    jhelper.get_model.assert_called_once_with("control-plane")
    shared_updater._condition.notify_all.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_model_and_notify_awaiters_reconnects_on_no_connection():
    jhelper = AsyncMock()
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._condition = AsyncMock()
    shared_updater._model_impl = None
    jhelper.get_model.side_effect = juju_connector.NoConnectionException
    with patch.object(shared_updater, "is_connected", Mock(return_value=False)):
        await shared_updater.reconnect_model_and_notify_awaiters()
    jhelper.reconnect.assert_called_once()
    shared_updater._condition.notify_all.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_model_and_notify_awaiters_silences_unknown_exceptions():
    jhelper = AsyncMock()
    jhelper.controller.is_connected = Mock(side_effect=ValueError)
    shared_updater = juju._SharedStatusUpdater(jhelper, "control-plane")
    shared_updater._condition = AsyncMock()
    shared_updater._model_impl = None
    with patch.object(shared_updater, "is_connected", Mock(return_value=False)):
        await shared_updater.reconnect_model_and_notify_awaiters()
    jhelper.reconnect.assert_not_called()
    shared_updater._condition.notify_all.assert_not_called()


@pytest.mark.asyncio
async def test_get_model_success(jhelper: juju.JujuHelper, model):
    result = await jhelper.get_model("control-plane")
    assert result == model
    jhelper.controller.get_model.assert_called_with("control-plane")


@pytest.mark.asyncio
async def test_get_model_not_found(jhelper: juju.JujuHelper):
    jhelper.controller.get_model.side_effect = Exception("HTTP 404")
    with pytest.raises(juju.ModelNotFoundException, match="Model 'missing' not found"):
        await jhelper.get_model("missing")


@pytest.mark.asyncio
async def test_get_model_connection_closed_error(jhelper: juju.JujuHelper):
    jhelper.reconnect = AsyncMock()
    jhelper.controller.get_model.side_effect = ConnectionClosedError(None, None)
    with pytest.raises(juju.JujuException, match="Failed to get model 'control-plane'"):
        await jhelper.get_model("control-plane")


@pytest.mark.asyncio
async def test_get_model_retry_on_connection_error(mocker, jhelper: juju.JujuHelper):
    jhelper.reconnect = AsyncMock()
    jhelper.controller.get_model.side_effect = [
        ConnectionClosedError(None, None),
        AsyncMock(),
    ]
    result = await jhelper.get_model("control-plane")
    assert result is not None
    assert jhelper.controller.get_model.call_count == 2


@pytest.mark.asyncio
async def test_get_model_retry_exceeded(jhelper: juju.JujuHelper):
    jhelper.reconnect = AsyncMock()
    jhelper.controller.get_model.side_effect = ConnectionClosedError(None, None)
    with pytest.raises(juju.JujuException, match="Failed to get model 'control-plane'"):
        await jhelper.get_model("control-plane")
