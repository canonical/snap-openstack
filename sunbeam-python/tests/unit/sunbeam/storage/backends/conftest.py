# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common fixtures and utilities for backend-specific tests."""

import pytest

from sunbeam.storage.backends.datacore.backend import DatacoreBackend
from sunbeam.storage.backends.datera.backend import DateraBackend
from sunbeam.storage.backends.dellpowermax.backend import DellpowermaxBackend
from sunbeam.storage.backends.dellpowerstore.backend import DellPowerstoreBackend
from sunbeam.storage.backends.dellpowervault.backend import DellPowerVaultBackend
from sunbeam.storage.backends.dellsc.backend import DellSCBackend
from sunbeam.storage.backends.dellunity.backend import DellunityBackend
from sunbeam.storage.backends.fujitsueternusdx.backend import FujitsueternusdxBackend
from sunbeam.storage.backends.hitachi.backend import HitachiBackend
from sunbeam.storage.backends.hpe3par.backend import HPEthreeparBackend
from sunbeam.storage.backends.hpexp.backend import HpexpBackend
from sunbeam.storage.backends.huawei.backend import HuaweiBackend
from sunbeam.storage.backends.ibmflashsystemcommon.backend import (
    IbmflashsystemcommonBackend,
)
from sunbeam.storage.backends.ibmflashsystemiscsi.backend import (
    IbmflashsystemiscsiBackend,
)
from sunbeam.storage.backends.ibmgpfs.backend import IbmgpfsBackend
from sunbeam.storage.backends.ibmibmstorage.backend import IbmibmstorageBackend
from sunbeam.storage.backends.ibmstorwizesvc.backend import IbmstorwizesvcBackend
from sunbeam.storage.backends.infinidat.backend import InfinidatBackend
from sunbeam.storage.backends.inspuras13000.backend import Inspuras13000Backend
from sunbeam.storage.backends.inspurinstorage.backend import InspurinstorageBackend
from sunbeam.storage.backends.kaminario.backend import KaminarioBackend
from sunbeam.storage.backends.linstor.backend import LinstorBackend
from sunbeam.storage.backends.macrosan.backend import MacrosanBackend
from sunbeam.storage.backends.necv.backend import NecvBackend
from sunbeam.storage.backends.netapp.backend import NetAppBackend
from sunbeam.storage.backends.nexenta.backend import NexentaBackend
from sunbeam.storage.backends.nimble.backend import NimbleBackend
from sunbeam.storage.backends.opene.backend import OpeneBackend
from sunbeam.storage.backends.prophetstor.backend import ProphetStorBackend
from sunbeam.storage.backends.purestorage.backend import PureStorageBackend
from sunbeam.storage.backends.qnap.backend import QnapBackend
from sunbeam.storage.backends.sandstone.backend import SandstoneBackend
from sunbeam.storage.backends.solidfire.backend import SolidFireBackend
from sunbeam.storage.backends.stx.backend import StxBackend
from sunbeam.storage.backends.synology.backend import SynologyBackend
from sunbeam.storage.backends.toyouacs5000.backend import Toyouacs5000Backend
from sunbeam.storage.backends.veritasaccess.backend import VeritasAccessBackend
from sunbeam.storage.backends.yadro.backend import YadroBackend
from sunbeam.storage.backends.zadara.backend import ZadaraBackend


@pytest.fixture
def hitachi_backend():
    """Provide a Hitachi backend instance."""
    return HitachiBackend()


@pytest.fixture
def purestorage_backend():
    """Provide a Pure Storage backend instance."""
    return PureStorageBackend()


@pytest.fixture
def dellsc_backend():
    """Provide a Dell Storage Center backend instance."""
    return DellSCBackend()


@pytest.fixture
def dellunity_backend():
    """Provide a Dell Unity backend instance."""
    return DellunityBackend()


@pytest.fixture
def huawei_backend():
    """Provide a Huawei OceanStor Dorado backend instance."""
    return HuaweiBackend()


@pytest.fixture
def datacore_backend():
    """Provide a DataCore backend instance."""
    return DatacoreBackend()


@pytest.fixture
def datera_backend():
    """Provide a Datera backend instance."""
    return DateraBackend()


@pytest.fixture
def dellpowermax_backend():
    """Provide a Dell PowerMax backend instance."""
    return DellpowermaxBackend()


@pytest.fixture
def dellpowervault_backend():
    """Provide a Dell PowerVault backend instance."""
    return DellPowerVaultBackend()


@pytest.fixture
def fujitsueternusdx_backend():
    """Provide a Fujitsu ETERNUS DX backend instance."""
    return FujitsueternusdxBackend()


@pytest.fixture
def nimble_backend():
    """Provide an HPE Nimble backend instance."""
    return NimbleBackend()


@pytest.fixture
def hpexp_backend():
    """Provide an HPE XP backend instance."""
    return HpexpBackend()


@pytest.fixture
def ibmflashsystemcommon_backend():
    """Provide an IBM FlashSystem Common backend instance."""
    return IbmflashsystemcommonBackend()


@pytest.fixture
def ibmflashsystemiscsi_backend():
    """Provide an IBM FlashSystem iSCSI backend instance."""
    return IbmflashsystemiscsiBackend()


@pytest.fixture
def ibmgpfs_backend():
    """Provide an IBM GPFS backend instance."""
    return IbmgpfsBackend()


@pytest.fixture
def ibmibmstorage_backend():
    """Provide an IBM Storage backend instance."""
    return IbmibmstorageBackend()


@pytest.fixture
def ibmstorwizesvc_backend():
    """Provide an IBM Storwize SVC backend instance."""
    return IbmstorwizesvcBackend()


@pytest.fixture
def inspuras13000_backend():
    """Provide an Inspur AS13000 backend instance."""
    return Inspuras13000Backend()


@pytest.fixture
def inspurinstorage_backend():
    """Provide an Inspur InStorage backend instance."""
    return InspurinstorageBackend()


@pytest.fixture
def kaminario_backend():
    """Provide a Kaminario backend instance."""
    return KaminarioBackend()


@pytest.fixture
def linstor_backend():
    """Provide a LINSTOR backend instance."""
    return LinstorBackend()


@pytest.fixture
def macrosan_backend():
    """Provide a MacroSAN backend instance."""
    return MacrosanBackend()


@pytest.fixture
def necv_backend():
    """Provide an NEC V backend instance."""
    return NecvBackend()


@pytest.fixture
def netapp_backend():
    """Provide a NetApp backend instance."""
    return NetAppBackend()


@pytest.fixture
def nexenta_backend():
    """Provide a Nexenta backend instance."""
    return NexentaBackend()


@pytest.fixture
def opene_backend():
    """Provide an Open-E backend instance."""
    return OpeneBackend()


@pytest.fixture
def prophetstor_backend():
    """Provide a ProphetStor backend instance."""
    return ProphetStorBackend()


@pytest.fixture
def qnap_backend():
    """Provide a QNAP backend instance."""
    return QnapBackend()


@pytest.fixture
def sandstone_backend():
    """Provide a Sandstone backend instance."""
    return SandstoneBackend()


@pytest.fixture
def stx_backend():
    """Provide a Stx backend instance."""
    return StxBackend()


@pytest.fixture
def synology_backend():
    """Provide a Synology backend instance."""
    return SynologyBackend()


@pytest.fixture
def toyouacs5000_backend():
    """Provide a Toyou ACS5000 backend instance."""
    return Toyouacs5000Backend()


@pytest.fixture
def veritasaccess_backend():
    """Provide a Veritas Access backend instance."""
    return VeritasAccessBackend()


@pytest.fixture
def yadro_backend():
    """Provide a Yadro backend instance."""
    return YadroBackend()


@pytest.fixture
def zadara_backend():
    """Provide a Zadara backend instance."""
    return ZadaraBackend()


@pytest.fixture
def dellpowerstore_backend():
    """Provide a Dell PowerStore backend instance."""
    return DellPowerstoreBackend()


@pytest.fixture
def infinidat_backend():
    """Provide an Infinidat backend instance."""
    return InfinidatBackend()


@pytest.fixture
def solidfire_backend():
    """Provide a NetApp SolidFire backend instance."""
    return SolidFireBackend()


@pytest.fixture
def hpe3par_backend():
    """Provide a HPE 3PAR Storage backend instance."""
    return HPEthreeparBackend()


# Single source of truth for all backend types and their factories.
BACKENDS = {
    "hitachi": HitachiBackend,
    "purestorage": PureStorageBackend,
    "dellsc": DellSCBackend,
    "datacore": DatacoreBackend,
    "datera": DateraBackend,
    "dellpowermax": DellpowermaxBackend,
    "dellpowervault": DellPowerVaultBackend,
    "fujitsueternusdx": FujitsueternusdxBackend,
    "hpexp": HpexpBackend,
    "ibmflashsystemcommon": IbmflashsystemcommonBackend,
    "ibmflashsystemiscsi": IbmflashsystemiscsiBackend,
    "ibmgpfs": IbmgpfsBackend,
    "nimble": NimbleBackend,
    "ibmibmstorage": IbmibmstorageBackend,
    "ibmstorwizesvc": IbmstorwizesvcBackend,
    "inspuras13000": Inspuras13000Backend,
    "inspurinstorage": InspurinstorageBackend,
    "kaminario": KaminarioBackend,
    "linstor": LinstorBackend,
    "macrosan": MacrosanBackend,
    "necv": NecvBackend,
    "netapp": NetAppBackend,
    "nexenta": NexentaBackend,
    "opene": OpeneBackend,
    "prophetstor": ProphetStorBackend,
    "qnap": QnapBackend,
    "sandstone": SandstoneBackend,
    "stx": StxBackend,
    "synology": SynologyBackend,
    "toyouacs5000": Toyouacs5000Backend,
    "veritasaccess": VeritasAccessBackend,
    "yadro": YadroBackend,
    "zadara": ZadaraBackend,
    "dellpowerstore": DellPowerstoreBackend,
    "solidfire": SolidFireBackend,
    "hpe3par": HPEthreeparBackend,
    "infinidat": InfinidatBackend,
    "dellunity": DellunityBackend,
    "huawei": HuaweiBackend,
}


@pytest.fixture(params=list(BACKENDS.keys()))
def any_backend(request):
    """Parametrized fixture that provides each backend type."""
    return BACKENDS[request.param]()
