import csv
import io
import json
from typing import Any


def parse(content: str, content_type: str = "text/plain") -> list[str]:
    """Parse raw content into text chunks based on content type."""
    handlers = {
        "text/plain": _parse_text,
        "application/json": _parse_json,
        "text/csv": _parse_csv,
    }
    handler = handlers.get(content_type, _parse_text)
    chunks = handler(content)
    # Filter empty chunks
    return [c.strip() for c in chunks if c.strip()]


def _parse_text(content: str) -> list[str]:
    """Split plain text into paragraph-level chunks."""
    paragraphs = content.split("\n\n")
    if len(paragraphs) == 1:
        # Single block — split on single newlines if long enough
        lines = content.split("\n")
        if len(lines) > 1:
            return lines
    return paragraphs


def _parse_json(content: str) -> list[str]:
    """Flatten JSON into text representations."""
    data = json.loads(content)
    chunks: list[str] = []
    _flatten_json(data, chunks, prefix="")
    return chunks


def _flatten_json(data: Any, chunks: list[str], prefix: str) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (str, int, float, bool)):
                chunks.append(f"{path}: {value}")
            else:
                _flatten_json(value, chunks, path)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (str, int, float, bool)):
                chunks.append(f"{prefix}[{i}]: {item}")
            elif isinstance(item, dict):
                # Combine dict items into a single chunk
                parts = []
                for k, v in item.items():
                    if isinstance(v, (str, int, float, bool)):
                        parts.append(f"{k}: {v}")
                if parts:
                    chunks.append("; ".join(parts))
                _flatten_json(item, chunks, f"{prefix}[{i}]")
            else:
                _flatten_json(item, chunks, f"{prefix}[{i}]")
    else:
        chunks.append(f"{prefix}: {data}")


def _parse_csv(content: str) -> list[str]:
    """Convert CSV rows into text representations."""
    reader = csv.DictReader(io.StringIO(content))
    chunks = []
    for row in reader:
        parts = [f"{k}: {v}" for k, v in row.items() if v]
        if parts:
            chunks.append("; ".join(parts))
    return chunks
