"""External data source integrations.

Provides polling clients for non-RSS data sources (aircraft tracking,
maritime AIS, satellite TLE, OpenStreetMap, CCTV, ICIJ Offshore Leaks,
OFAC Sanctions) that produce IngestedDocument objects compatible with
the standard pipeline.
"""

from .icij_offshore import ICIJOffshoreSource
from .ofac_sanctions import OFACSanctionsSource
from .irs_exempt_orgs import IRSExemptOrgsSource
from .nc_sos_business import NCSoSBusinessSource
from .nc_register_of_deeds import NCRegisterOfDeedsSource

__all__ = [
    "ICIJOffshoreSource",
    "OFACSanctionsSource",
    "IRSExemptOrgsSource",
    "NCSoSBusinessSource",
    "NCRegisterOfDeedsSource",
]
