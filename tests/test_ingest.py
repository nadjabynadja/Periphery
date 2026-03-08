import numpy as np

from periphery.ingest.parsers import parse
from periphery.ingest.store import FAISSStore


def test_parse_plain_text():
    text = "Hello world\n\nThis is a test\n\nThird paragraph"
    chunks = parse(text, "text/plain")
    assert len(chunks) == 3
    assert chunks[0] == "Hello world"


def test_parse_json():
    import json
    data = json.dumps({"name": "Alice", "age": 30, "city": "NYC"})
    chunks = parse(data, "application/json")
    assert len(chunks) > 0
    assert any("Alice" in c for c in chunks)


def test_parse_csv():
    csv_data = "name,age,city\nAlice,30,NYC\nBob,25,LA"
    chunks = parse(csv_data, "text/csv")
    assert len(chunks) == 2
    assert "Alice" in chunks[0]


def test_store_add_and_search(tmp_store, sample_vectors, sample_ids):
    tmp_store = FAISSStore(dim=384, index_path=tmp_store.index_path)
    tmp_store.add(sample_ids[:5], sample_vectors[:5])
    assert tmp_store.total == 5

    results = tmp_store.search(sample_vectors[0], top_k=3)
    assert len(results) == 3
    # First result should be the query itself (exact match)
    assert results[0][0] == "doc_0"
    assert results[0][1] > 0.99


def test_store_persist_and_reload(tmp_store, sample_vectors, sample_ids):
    tmp_store.add(sample_ids[:10], sample_vectors[:10])
    tmp_store.save()

    reloaded = FAISSStore(dim=384, index_path=tmp_store.index_path)
    assert reloaded.total == 10
    results = reloaded.search(sample_vectors[0], top_k=1)
    assert results[0][0] == "doc_0"


def test_store_get_all_vectors(tmp_store, sample_vectors, sample_ids):
    tmp_store.add(sample_ids[:5], sample_vectors[:5])
    all_vecs = tmp_store.get_all_vectors()
    assert all_vecs.shape == (5, 384)
    np.testing.assert_allclose(all_vecs[0], sample_vectors[0], atol=1e-6)


def test_store_get_all_ids(tmp_store, sample_vectors, sample_ids):
    tmp_store.add(sample_ids[:5], sample_vectors[:5])
    ids = tmp_store.get_all_ids()
    assert ids == sample_ids[:5]
