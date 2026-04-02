"""Property records lookup — voters, donors, businesses at an address.

Uses the Periphery analytical DB + CivicVoice data for NC addresses.
Falls back to geocoding for address normalization.
"""

import logging
import os
from typing import Optional

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

CIVICVOICE_BASE = os.environ.get("CIVICVOICE_API_URL", "http://localhost:8100")
PHOTON_BASE = "https://photon.komoot.io"


async def reverse_geocode(lat: float, lng: float) -> Optional[dict]:
    """Reverse geocode coordinates to get address components."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{PHOTON_BASE}/reverse",
                params={"lat": lat, "lon": lng, "limit": 1},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("features"):
                    props = data["features"][0]["properties"]
                    return {
                        "street": props.get("street", ""),
                        "housenumber": props.get("housenumber", ""),
                        "city": props.get("city", ""),
                        "state": props.get("state", ""),
                        "postcode": props.get("postcode", ""),
                        "country": props.get("country", ""),
                    }
    except Exception as e:
        logger.debug(f"Reverse geocode failed: {e}")
    return None


def _parse_address_parts(address: str) -> tuple[str, str, str]:
    """Extract street, city, and zip code from an address string.

    Returns (street_parts, city, zip_code).
    """
    street_parts = address.split(",")[0].strip() if "," in address else address
    zip_code = ""
    city = ""

    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        city = parts[1].strip()
    # Extract zip from last part
    for part in reversed(parts):
        digits = "".join(c for c in part if c.isdigit())
        if len(digits) == 5:
            zip_code = digits
            break

    return street_parts, city, zip_code


async def _query_voters(voter_db: str, street_parts: str, zip_code: str) -> tuple[list[dict], list[str]]:
    """Query voter DB for residents at a street address.

    Returns (voters_list, owner_names).
    """
    voters: list[dict] = []
    owners: list[str] = []

    if not street_parts or not os.path.exists(voter_db):
        return voters, owners

    # Build LIKE pattern: replace whitespace runs with % for flexible matching
    words = street_parts.upper().split()
    like_pattern = "%" + "%".join(words) + "%"

    async with aiosqlite.connect(voter_db) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT first_name, middle_name, last_name, party_cd,
                   registr_dt, status_cd, res_street_address,
                   res_city, zip_code
            FROM voters
            WHERE res_street_address LIKE ?
        """
        params: list[str] = [like_pattern]

        if zip_code:
            query += " AND zip_code LIKE ?"
            params.append(f"{zip_code}%")

        query += " LIMIT 20"

        seen_names: set[str] = set()
        async with db.execute(query, params) as cursor:
            async for row in cursor:
                name = f"{row['first_name']} {row['middle_name'] or ''} {row['last_name']}".strip()
                name = " ".join(name.split())

                if name in seen_names:
                    continue
                seen_names.add(name)

                # Count voting history
                hist_count = 0
                try:
                    async with db.execute(
                        "SELECT COUNT(*) as cnt FROM voter_history "
                        "WHERE voter_reg_num = ("
                        "  SELECT voter_reg_num FROM voters "
                        "  WHERE first_name = ? AND last_name = ? LIMIT 1"
                        ")",
                        [row["first_name"], row["last_name"]],
                    ) as hist_cur:
                        hist_row = await hist_cur.fetchone()
                        if hist_row:
                            hist_count = hist_row["cnt"]
                except Exception:
                    pass

                voters.append({
                    "name": name,
                    "party": row["party_cd"] or "UNA",
                    "registrationDate": row["registr_dt"] or "",
                    "status": row["status_cd"] or "A",
                    "votingHistory": hist_count,
                })

        # Owners = unique last names at address (property proxy)
        owner_names: set[str] = set()
        for v in voters:
            last = v["name"].split()[-1] if v["name"] else ""
            if last:
                owner_names.add(last + " HOUSEHOLD")
        owners = list(owner_names)

    return voters, owners


async def _query_donors(finance_db: str, voter_names: list[str]) -> list[dict]:
    """Query finance DB for campaign contributions matching voter names."""
    donors: list[dict] = []

    if not voter_names or not os.path.exists(finance_db):
        return donors

    async with aiosqlite.connect(finance_db) as db:
        db.row_factory = aiosqlite.Row

        for name in voter_names[:5]:
            parts = name.split()
            if len(parts) < 2:
                continue
            last = parts[-1]
            first = parts[0]

            async with db.execute(
                """
                SELECT name, SUM(transaction_amt) as total,
                       COUNT(DISTINCT cmte_id) as recipients,
                       MAX(transaction_dt) as last_dt
                FROM fec_contributions
                WHERE name LIKE ? AND name LIKE ?
                GROUP BY name
                LIMIT 3
                """,
                [f"%{last}%", f"%{first}%"],
            ) as cursor:
                async for row in cursor:
                    if row["total"] and row["total"] > 0:
                        donors.append({
                            "name": row["name"] or name,
                            "totalAmount": round(row["total"], 2),
                            "recipientCount": row["recipients"] or 0,
                            "lastDonation": row["last_dt"] or "",
                        })

    return donors


async def lookup_property_records(
    lat: float, lng: float, address: Optional[str] = None
) -> dict:
    """Look up property records at a location.

    Queries CivicVoice voter + finance DBs for matching records.
    """
    # Get address if not provided
    if not address:
        geo = await reverse_geocode(lat, lng)
        if geo:
            parts = [geo.get("housenumber", ""), geo.get("street", "")]
            address = " ".join(p for p in parts if p).strip()
            if geo.get("city"):
                address += f", {geo['city']}"
            if geo.get("state"):
                address += f", {geo['state']}"
            if geo.get("postcode"):
                address += f" {geo['postcode']}"

    if not address:
        address = f"{lat}, {lng}"

    result: dict = {
        "address": address,
        "owners": [],
        "voters": [],
        "donors": [],
        "businesses": [],
        "assessedValue": None,
        "parcelId": None,
    }

    street_parts, city, zip_code = _parse_address_parts(address)

    # Query CivicVoice voter DB
    try:
        voter_db = os.environ.get("VOTER_DB_PATH", "/root/Civic-Voice/data/voter.db")
        voters, owners = await _query_voters(voter_db, street_parts, zip_code)
        result["voters"] = voters
        result["owners"] = owners
    except Exception as e:
        logger.error(f"Voter DB query failed: {e}")

    # Query CivicVoice finance DB for donors
    try:
        finance_db = os.environ.get("FINANCE_DB_PATH", "/root/Civic-Voice/data/finance.db")
        donor_names = [v["name"].upper() for v in result["voters"]]
        result["donors"] = await _query_donors(finance_db, donor_names)
    except Exception as e:
        logger.error(f"Finance DB query failed: {e}")

    return result
