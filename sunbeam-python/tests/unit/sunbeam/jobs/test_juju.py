# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import yaml
from juju.application import Application
from juju.model import Model
from juju.unit import Unit

import sunbeam.jobs.juju as juju

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
async def test_jhelper_get_unit(jhelper: juju.JujuHelper, units):
    await jhelper.get_unit("k8s/0", "control-plane")
    units.get.assert_called_with("k8s/0")


@pytest.mark.asyncio
async def test_jhelper_get_unit_missing(jhelper: juju.JujuHelper):
    name = "mysql/0"
    model = "control-plane"
    with pytest.raises(
        juju.UnitNotFoundException,
        match=f"Unit {name!r} is missing from model {model!r}",
    ):
        await jhelper.get_unit(name, model)


@pytest.mark.asyncio
async def test_jhelper_get_unit_invalid_name(jhelper: juju.JujuHelper):
    with pytest.raises(
        ValueError,
        match=(
            "Name 'k8s' has invalid format, "
            "should be a valid unit of format application/id"
        ),
    ):
        await jhelper.get_unit("k8s", "control-plane")


@pytest.mark.asyncio
async def test_jhelper_get_leader_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = "k8s"
    unit = await jhelper.get_leader_unit(app, "control-plane")
    assert unit is not None
    assert applications.get.called_with(app)


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
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    app = await jhelper.get_application("k8s", "control-plane")
    assert app is not None
    assert applications.get.called_with("k8s")


@pytest.mark.asyncio
async def test_jhelper_get_application_missing(jhelper: juju.JujuHelper):
    model = "control-plane"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application missing from model: {model!r}",
    ):
        await jhelper.get_application("mysql", model)


@pytest.mark.asyncio
async def test_jhelper_add_unit(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    await jhelper.add_unit("k8s", "control-plane")
    applications["k8s"].add_unit.assert_called_with(1, None)


@pytest.mark.asyncio
async def test_jhelper_add_unit_to_machine(
    jhelper: juju.JujuHelper, applications: dict[str, Application]
):
    await jhelper.add_unit("k8s", "control-plane", machine="0")
    applications["k8s"].add_unit.assert_called_with(1, "0")


@pytest.mark.asyncio
async def test_jhelper_add_unit_to_missing_application(
    jhelper: juju.JujuHelper,
):
    name = "mysql"
    model = "control-plane"
    with pytest.raises(
        juju.ApplicationNotFoundException,
        match=f"Application missing from model: {model!r}",
    ):
        await jhelper.add_unit(name, model)


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
    (
        "wait_units_ready",
        "k8s/0",
        "units k8s/0",
        [{"agent": "idle", "workload": "blocked"}],
    ),
]

test_data_custom_status = [
    ("wait_application_ready", "mk8s", ["unknown"]),
    ("wait_units_ready", "k8s/1", {"agent": "unknown", "workload": "unknown"}),
]

test_data_missing = [
    ("wait_application_ready", "mysql"),
    ("wait_units_ready", "mysql/0"),
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
    with patch.object(juju, "CharmHub") as p:
        charmhub = AsyncMock()
        charmhub.info.return_value = cmd_out
        p.return_value = charmhub
        revno = await jhelper.get_available_charm_revision(
            "openstack", "k8s", "legacy/edge"
        )
        assert revno == 121
