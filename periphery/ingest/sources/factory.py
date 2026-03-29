"""Factory for constructing DataSource instances from Settings."""

from __future__ import annotations

from periphery.config import Settings

from .adsb_exchange import ADSBExchangeSource
from .base import DataSource
from .cctv import CCTVSource
from .celestrak import CelesTrakSource
from .gdelt_doc import GDELTDocSource
from .icij_offshore import ICIJOffshoreSource
from .maritime import MaritimeSource
from .ofac_sanctions import OFACSanctionsSource
from .opensky import OpenSkySource
from .nc_voter import NCVoterSource
from .fec_contributions import FECContributionsSource
from .nc_campaign_finance import NCCampaignFinanceSource
from .nc_parcels import NCParcelsSource
from .openstreetmap import OpenStreetMapSource


def _parse_csv(value: str) -> list[str]:
    """Split comma-separated string into trimmed non-empty parts."""
    return [s.strip() for s in value.split(",") if s.strip()]


def _parse_float_tuple(value: str) -> tuple[float, ...] | None:
    """Parse comma-separated floats, or return None."""
    parts = _parse_csv(value)
    if not parts:
        return None
    try:
        return tuple(float(p) for p in parts)
    except (ValueError, TypeError):
        return None


def build_sources(settings: Settings) -> list[DataSource]:
    """Construct all configured DataSource instances from app settings."""
    sources: list[DataSource] = []

    # OpenSky Network
    bbox = _parse_float_tuple(settings.opensky_bbox)
    sources.append(
        OpenSkySource(
            bbox=bbox if bbox and len(bbox) == 4 else None,
            username=settings.opensky_username or None,
            password=settings.opensky_password or None,
            poll_interval=settings.opensky_poll_interval,
            enabled=settings.opensky_enabled,
        )
    )

    # ADS-B Exchange via Position-API
    sources.append(
        ADSBExchangeSource(
            position_api_url=settings.adsb_position_api_url,
            icao_watchlist=_parse_csv(settings.adsb_icao_watchlist),
            poll_interval=settings.adsb_poll_interval,
            enabled=settings.adsb_enabled,
        )
    )

    # Maritime via Position-API
    sources.append(
        MaritimeSource(
            position_api_url=settings.maritime_position_api_url,
            mmsi_watchlist=_parse_csv(settings.maritime_mmsi_watchlist),
            watch_areas=_parse_csv(settings.maritime_watch_areas),
            poll_interval=settings.maritime_poll_interval,
            enabled=settings.maritime_enabled,
        )
    )

    # CelesTrak TLE
    norad_ids: list[int] = []
    for s in _parse_csv(settings.celestrak_norad_ids):
        try:
            norad_ids.append(int(s))
        except ValueError:
            pass
    sources.append(
        CelesTrakSource(
            groups=_parse_csv(settings.celestrak_groups) or None,
            norad_ids=norad_ids or None,
            poll_interval=settings.celestrak_poll_interval,
            enabled=settings.celestrak_enabled,
        )
    )

    # OpenStreetMap
    osm_bbox = _parse_float_tuple(settings.osm_bbox)
    sources.append(
        OpenStreetMapSource(
            bbox=osm_bbox if osm_bbox and len(osm_bbox) == 4 else None,
            feature_types=_parse_csv(settings.osm_feature_types) or None,
            overpass_url=settings.osm_overpass_url,
            poll_interval=settings.osm_poll_interval,
            enabled=settings.osm_enabled,
        )
    )

    # Public CCTV
    sources.append(
        CCTVSource(
            dot_endpoints=_parse_csv(settings.cctv_dot_endpoints) or None,
            poll_interval=settings.cctv_poll_interval,
            enabled=settings.cctv_enabled,
        )
    )

    # ICIJ Offshore Leaks
    sources.append(
        ICIJOffshoreSource(
            poll_interval=settings.icij_poll_interval,
            enabled=settings.icij_enabled,
            node_types=_parse_csv(settings.icij_node_types) or None,
            data_dir=settings.icij_data_dir,
        )
    )

    # OFAC Sanctions Lists
    sources.append(
        OFACSanctionsSource(
            poll_interval=settings.ofac_poll_interval,
            enabled=settings.ofac_enabled,
            include_consolidated=settings.ofac_include_consolidated,
        )
    )

    # NC Voter Registration
    sources.append(
        NCVoterSource(
            data_dir=settings.nc_voter_data_dir,
            poll_interval=settings.nc_voter_poll_interval,
            enabled=settings.nc_voter_enabled,
        )
    )

    # FEC Individual Contributions
    sources.append(
        FECContributionsSource(
            data_dir=settings.fec_data_dir,
            cycles=_parse_csv(settings.fec_cycles),
            state_filter=settings.fec_state_filter,
            poll_interval=settings.fec_poll_interval,
            enabled=settings.fec_enabled,
        )
    )

    # NC Campaign Finance
    sources.append(
        NCCampaignFinanceSource(
            poll_interval=settings.nc_campaign_finance_poll_interval,
            enabled=settings.nc_campaign_finance_enabled,
        )
    )

    # NC Property Records (Parcels)
    sources.append(
        NCParcelsSource(
            poll_interval=settings.nc_parcels_poll_interval,
            enabled=settings.nc_parcels_enabled,
        )
    )

    # GDELT DOC 2.0
    sources.append(
        GDELTDocSource(
            poll_interval=settings.gdelt_poll_interval,
            enabled=settings.gdelt_enabled,
            max_articles_per_query=settings.gdelt_max_articles_per_query,
        )
    )

    return sources
