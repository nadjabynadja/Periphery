# Security Hardening Specification
**Priority:** CRITICAL — parallel with NC data integration
**Author:** Nadja Daffron / Kate Warne
**Date:** 2026-03-29

## Design Principles
- Durable to ANY classification: PUBLIC, PII, CUI, PROPRIETARY, CLASSIFIED
- Build once, build right
- Must support on-prem deployment (client-specific keys, audit logs, encryption)
- Nothing hardcoded to our instance

## 1. Data Classification Framework

Every document gets a classification tag at ingest:
- **PUBLIC** — open source news, GDELT, RSS articles
- **PII** — voter records, donor records, any record with personal identifying info
- **CUI** — Controlled Unclassified Information (future: government contract data)
- **PROPRIETARY** — client data on on-prem instances
- **CLASSIFIED** — future Phase II (schema must support it now)

Rules:
- Classification is a first-class field on IngestedDocument, not just metadata
- Classification propagates: cluster inherits highest classification of any member
- Query results inherit highest classification of any contributing source

## 2. Authentication

Require auth on ALL endpoints. Zero unauthenticated access.

Three credential types:
- **Admin key** — full system access, config, ingest, restart, user management
- **Analyst key** — query, search, entities, clusters, relationships, export. Scoped by classification.
- **Ingest key** — write to ingest endpoints only, no query access

Key attributes:
- Unique ID, creation date, expiration date
- Scope (list of allowed classifications)
- Rate limit
- Human-readable label

Failed auth: logged and rate-limited (lockout after 10 failures)

## 3. MCP Server Auth

API key required for all MCP tool calls. Keys scoped by role:
- **Kate:** full access (all tools, all classifications)
- **Eloise:** read-only (query, search, clusters, entities, emerging, anomalies, trajectories, critic_scores, legibility_gradient, query_history). No ingest, no admin. Scoped to PUBLIC + PII.

Log all MCP tool calls: timestamp, caller key ID, tool name, parameters, classification of returned data.

## 4. Audit Log (Immutable)

Separate from application logs. Append-only.

Records:
- All auth events (success, failure, key used)
- All data access touching PII+ (query text, result count, classifications)
- All ingest events (source, record count, classification)
- All admin actions (config changes, key creation/revocation, restarts)
- All export/download events

Retention: 1 year minimum (NIST 800-171 / CMMC)
Format: structured JSON, one event per line

## 5. Encryption

- At rest: LUKS at filesystem level (mandatory). SQLCipher optional.
- In transit: HTTPS enforced, no HTTP fallback. Internal MCP via SSH tunnel.
- Key management: keys stored separately from encrypted data. Not in repo. Not in .env on same disk.

## 6. Network Hardening

- Firewall: only 443 (HTTPS), 22 (SSH), tunnel port open
- API not directly on port 8000 — Nginx/Caddy reverse proxy with TLS
- Rate limiting at proxy + application level

## 7. Data Handling Rules

- PII+: never in error messages, debug logs, or API error responses
- Query results with PII include classification header in response
- PII export requires explicit confirmation (not just GET)
- Bulk export >1,000 PII records requires admin key

## Build Order
1. Items 1-3 first (access model foundation)
2. Items 4-7 follow
