"""Continuous document processing pipeline.

Watches periphery_documents.db for new documents and drives them through
enrichment, embedding, and crystallization automatically using a linear
state machine: pending -> enriching -> enriched -> embedding -> embedded -> crystallized
"""
