# ICIJ Offshore Leaks — Crystallizer Intelligence Analysis

**Date:** 2026-03-20
**Analyst:** James Beard (Periphery AI)
**Corpus:** 1,391,517 ICIJ documents + 6,744 enriched through NLP pipeline + 2,222 news documents

---

## 1. Dataset Overview

| Node Type | Count |
|-----------|-------|
| Entities (companies) | 813,393 |
| Officers (people) | 554,404 |
| Intermediaries | 23,720 |
| **Total** | **1,391,517** |

### Source Datasets
| Dataset | Count |
|---------|-------|
| Panama Papers | 356,196 |
| Offshore Leaks | 209,911 |
| Bahamas Leaks | 183,534 |
| Paradise Papers — Malta registry | 179,365 |
| Paradise Papers — Barbados registry | 119,894 |
| Paradise Papers — Appleby | 101,481 |
| Paradise Papers — Aruba registry | 79,629 |
| Paradise Papers — Nevis registry | 70,763 |
| Pandora Papers — Alcogal | 33,686 |

### Top Jurisdictions (Entities)
| Jurisdiction | Count |
|-------------|-------|
| Bahamas | 209,665 |
| British Virgin Islands | 172,291 |
| Malta | 83,909 |
| Saint Kitts & Nevis | 70,597 |
| Panama | 48,596 |
| Aruba | 48,537 |
| Barbados | 40,833 |
| Seychelles | 16,885 |
| Samoa | 15,019 |

### Service Providers
| Provider | Entities Created |
|----------|-----------------|
| Mossack Fonseca | 213,527 |
| Portcullis Trustnet | 61,068 |
| Commonwealth Trust Limited | 44,385 |
| Appleby | 24,925 |

### Entity Status
| Status | Count |
|--------|-------|
| Active | 115,885 |
| Defaulted | 100,089 |
| Dissolved | 24,082 |
| Dead | 23,089 |
| Struck/Defunct | 19,486 |

**115,885 entities remain marked as Active.**

---

## 2. OFAC Sanctions ↔ ICIJ Cross-Matches

Six exact name matches between OFAC-sanctioned entities and ICIJ offshore structures:

### GPB-DI Holdings Limited (Gazprombank subsidiary)
- **OFAC:** Sanctioned under UKRAINE-EO13662 and RUSSIA-EO14024. State-owned enterprise. Directive 1 subject with secondary sanctions risk. Registered in Cyprus (HE145737).
- **ICIJ:** Officer record from Panama Papers. Linked to Cyprus.
- **Significance:** Gazprombank's offshore subsidiary had a Mossack Fonseca relationship — the offshore structure predates sanctions, suggesting pre-existing financial opacity.

### Rose Group Limited
- **OFAC:** Sanctioned under UKRAINE-EO13662. Linked to rosegroup.ru. Connected to State Corporation Bank for Development (VEB).
- **ICIJ:** Active BVI entity. Incorporated 02-AUG-2007. Set up by Portcullis Trustnet. From the Offshore Leaks dataset.
- **Significance:** Still listed as **Active** in BVI despite being a sanctioned Russian state-linked entity. BVI registrar may not have flagged this.

### CNOOC Limited
- **OFAC:** Sanctioned under CMIC-EO13959 (Chinese military-industrial complex). Equity ticker 00883 HK.
- **ICIJ:** Appears 3x in Offshore Leaks — as entity, officer, and intermediary. China-linked.
- **Significance:** China's national offshore oil company had extensive offshore structuring through the same networks as other state actors.

### China Communications Construction Company Limited
- **OFAC:** Sanctioned under CMIC-EO13959.
- **ICIJ:** Entity in Paradise Papers — Malta corporate registry. Registered in Malta.
- **Significance:** Chinese military-linked firm operating through Maltese corporate structures.

---

## 3. Current Events Correlation

### Countries in Current News vs ICIJ Exposure

| Country | News Articles | Active ICIJ Entities |
|---------|--------------|---------------------|
| Iran | 419 | 381 |
| Russia | 318 | (4,197 via Mossack Fonseca alone) |
| China | 218 | 3,213+ (Mossack Fonseca) |
| Israel | 308 | 381 |
| UAE | 47 | 2,753 |
| Ukraine | 229 | 188 |
| Lebanon | 56 | 194 |
| Kuwait | 59 | 33 |
| Saudi Arabia | 60 | 38 |

### Kuwait Petroleum Corporation — Active Conflict + Offshore Structure
- **ICIJ:** Active entity in Panama Papers. Incorporated 21-MAY-2014 in British Anguilla. Set up by Mossack Fonseca. Country link: China.
- **News:** KPC's Mina al-Ahmadi refinery hit by drone in current Iran-related conflict. Multiple news sources (Al Jazeera, Middle East Eye, JPost, Al-Monitor).
- **Significance:** A state oil company under active military attack has a dormant offshore structure in a known secrecy jurisdiction. Worth monitoring for insurance/reinsurance or asset-shielding implications.

### NAFTIRAN Intertrade Co. (NICO) Limited — Iranian Oil Trading Arm
- **ICIJ:** 3 records across Panama Papers and Paradise Papers (Nevis). Incorporated in Saint Kitts & Nevis (19-AUG-2016). Also appears as officer linked to Jersey.
- **Context:** NICO is the offshore trading subsidiary of National Iranian Oil Company (NIOC). Its presence in the Panama Papers and subsequent re-registration in Nevis (2016, post-JCPOA) suggests ongoing offshore structuring during the sanctions relief period. Not currently on OFAC's consolidated list but has been sanctioned by the EU.
- **Significance:** With Iran currently engaged in conflict (per news corpus), NICO's offshore trail is a potential sanctions evasion indicator.

---

## 4. Chinese Military-Industrial Entities in ICIJ

| Entity | ICIJ Records | Status | Notable |
|--------|-------------|--------|---------|
| COSCO | 46 | 2 Active (BVI) | State shipping company; 2 active offshore entities |
| CITIC | 78 | 2 Active (Seychelles) | State investment corporation |
| Huawei | 7 | 3 Active (BVI, Singapore) | Includes "HUAWEI LTD." in BVI — Active |
| BGI | 11 | 1 Active (Anguilla) | Genomics company with security concerns |

All are Panama Papers or Offshore Leaks vintage (pre-2015 data), but several entities remain **Active** in their incorporation jurisdictions.

---

## 5. Mossack Fonseca Client Geography

Top country links for Mossack Fonseca-created entities:

| Country | Entities |
|---------|----------|
| Switzerland | 37,911 |
| Hong Kong | 37,911 |
| Panama | 15,717 |
| Jersey | 14,331 |
| Luxembourg | 10,840 |
| United Kingdom | 9,619 |
| UAE | 7,268 |
| Bahamas | 4,974 |
| Russia | 4,197 |
| Singapore | 4,081 |
| Cyprus | 3,613 |
| China | 3,213 |

---

## 6. Crystallizer Observations

### Anomaly Detection
The crystallizer flagged 1,002 anomalies in the current snapshot. Top anomaly types:
- **novel_relationship** — entities appearing in new relational contexts
- **structural** — documents that don't fit existing cluster patterns

The highest-scored anomalies are concentrated in Middle East conflict reporting (Iran, Kuwait, Bahrain, Israel) — reflecting rapid emergence of new relational patterns the crystallizer hasn't seen before in the training corpus.

### Cross-Source Entity Resolution
The crystallizer resolved 86 canonical entities that appear in both ICIJ and news/OFAC sources. Most are geographic entities (countries, jurisdictions), but the substantive organizational matches (Rose Group, GPB-DI, CNOOC, China Communications) represent genuine intelligence signals.

### Notable Gap
The crystallizer's entity resolution struggles with ICIJ data because:
1. ICIJ documents are structured records (not prose) — NER extracts fragments like "LTD" as entities
2. Company names in ICIJ vs. news mentions often differ in format (e.g., "CNOOC Limited" vs "CNOOC")
3. Future improvement: implement fuzzy name matching for corporate entity resolution

---

## 7. Recommendations

1. **Priority investigation targets:** Rose Group Limited (active BVI + Russian sanctions), NAFTIRAN INTERTRADE (Iranian oil trading), Kuwait Petroleum Corp (active conflict + offshore structure)
2. **Data quality:** Add fuzzy corporate name matching to the entity resolution pipeline — would dramatically increase ICIJ↔news cross-referencing
3. **Index optimization:** Add a SQLite index on `json_extract(metadata, "$.countries")` and `json_extract(metadata, "$.status")` for the documents table — current ICIJ queries take minutes
4. **Enrichment gap:** Only 6,744 of 1,391,517 ICIJ documents have been through the NLP enrichment pipeline — batch-enriching more would surface additional cross-source links

---

*Generated by Periphery Intelligence Console, 2026-03-20*
