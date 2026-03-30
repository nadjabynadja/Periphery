"""Continuous document processing pipeline.

Reads pending documents from collection databases (rss.db, gdelt.db,
sanctions.db) and drives them through enrichment, embedding, and
crystallization into analytical.db using a linear state machine:
pending -> enriching -> enriched -> embedding -> embedded -> crystallized
"""
