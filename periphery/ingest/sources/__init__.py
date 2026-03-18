"""External data source integrations.

Provides polling clients for non-RSS data sources (aircraft tracking,
maritime AIS, satellite TLE, OpenStreetMap, CCTV, ICIJ Offshore Leaks,
OFAC Sanctions) that produce IngestedDocument objects compatible with
the standard pipeline.
"""

from .icij_offshore import ICIJOffshoreSource
from .ofac_sanctions import OFACSanctionsSource

__all__ = [
    "ICIJOffshoreSource",
    "OFACSanctionsSource",
]
