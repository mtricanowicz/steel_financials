import sys
from types import SimpleNamespace

import pytest

from sec_pipeline import summarize
from sec_pipeline.embed import _near_duplicate, retrieve_passages


def _install_collection(monkeypatch, response):
    class Collection:
        def query(self, **kwargs):
            return response

    client = type("Client", (), {"get_collection": lambda self, _: Collection()})()
    monkeypatch.setattr("sec_pipeline.embed._client", lambda: client)


def test_multi_query_support_increases_passage_rank(monkeypatch):
    _install_collection(
        monkeypatch,
        {
            "documents": [
                ["Steel shipments and energy costs changed materially."],
                ["A new mill expansion was announced.", "Steel shipments and energy costs changed materially."],
            ],
            "metadatas": [
                [{"source_id": "10-Q:one", "chunk_index": 1}],
                [{"source_id": "8-K:two", "chunk_index": 1}, {"source_id": "10-Q:one", "chunk_index": 1}],
            ],
            "distances": [[0.2], [0.1, 0.3]],
        },
    )

    passages = retrieve_passages("demo", ["financial", "operations"], lambda texts: [[1.0] for _ in texts], k=2)

    assert passages[0][0] == "Steel shipments and energy costs changed materially."
    assert passages[0][1]["query_support"] == 2
    assert passages[0][1]["query_indices"] == [0, 1]


def test_query_weights_change_fused_passage_rank(monkeypatch):
    _install_collection(
        monkeypatch,
        {
            "documents": [["Financial result passage."], ["Material 8-K event passage."]],
            "metadatas": [[{"source_id": "10-Q:one"}], [{"source_id": "8-K:two"}]],
            "distances": [[0.1], [0.1]],
        },
    )

    passages = retrieve_passages(
        "demo", ["financial", "event"], lambda texts: [[1.0] for _ in texts], k=1,
        query_weights={0: 0.5, 1: 1.5},
    )

    assert passages[0][0] == "Material 8-K event passage."


def test_identical_text_from_different_sources_is_preserved(monkeypatch):
    _install_collection(
        monkeypatch,
        {
            "documents": [["The company reported no material changes."], ["The company reported no material changes."]],
            "metadatas": [[{"source_id": "10-Q:first", "chunk_index": 2}], [{"source_id": "10-Q:second", "chunk_index": 2}]],
            "distances": [[0.1], [0.2]],
        },
    )

    passages = retrieve_passages("demo", ["first", "second"], lambda texts: [[1.0] for _ in texts], k=1)

    assert {metadata["source_id"] for _, metadata in passages} == {"10-Q:first", "10-Q:second"}


def test_context_limits_secondary_channels(monkeypatch):
    monkeypatch.setattr(summarize, "MAX_CONTEXT_TOKENS", 100)
    passages = [
        ("First labor disclosure.", {"query_index": 3, "source_id": "10-Q:one"}),
        ("Second labor disclosure.", {"query_index": 3, "source_id": "10-Q:two"}),
        ("Shipment disclosure.", {"query_index": 2, "source_id": "10-Q:three"}),
    ]

    context = summarize._build_context(passages)

    assert "First labor disclosure." in context
    assert "Second labor disclosure." not in context
    assert "Shipment disclosure." in context


def test_context_reserves_guidance_passage(monkeypatch):
    monkeypatch.setattr(summarize, "MAX_CONTEXT_TOKENS", 23)
    passages = [
        ("financial result " * 20, {"query_index": 0, "source_id": "10-Q:one"}),
        (
            "Management expects next-quarter shipments of 2.0 to 2.2 million tons.",
            {
                "query_index": summarize.GUIDANCE_QUERY_INDEX,
                "query_indices": [summarize.GUIDANCE_QUERY_INDEX],
                "source_id": "8-K:one:EX-99.1",
            },
        ),
    ]

    context = summarize._build_context(passages)

    assert "Management expects next-quarter shipments" in context


def test_length_limited_summary_retries_with_compaction_prompt(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="length", message=SimpleNamespace(content="Incomplete")
                )]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="Complete.")
                )]
            ),
        ]
    )
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return next(responses)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            OpenAI=lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        ),
    )
    monkeypatch.setattr(summarize, "collection_size", lambda _: 1)
    monkeypatch.setattr(
        summarize,
        "retrieve_passages",
        lambda *args, **kwargs: [("Supported source text.", {"form": "10-Q"})],
    )

    assert summarize.summarize_period("NUE", "2024Q2", "demo", lambda _: [[1.0]]) == "Complete."
    assert len(calls) == 2
    assert summarize.COMPACTION_INSTRUCTION in calls[1]["messages"][1]["content"]


def test_second_length_limited_summary_is_rejected(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="Incomplete"))]
    )

    class Completions:
        def create(self, **kwargs):
            return response

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(
            OpenAI=lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        ),
    )
    monkeypatch.setattr(summarize, "collection_size", lambda _: 1)
    monkeypatch.setattr(
        summarize,
        "retrieve_passages",
        lambda *args, **kwargs: [("Supported source text.", {"form": "10-Q"})],
    )

    with pytest.raises(RuntimeError, match="exceeded the completion limit"):
        summarize.summarize_period("NUE", "2024Q2", "demo", lambda _: [[1.0]])


def test_near_duplicate_helper_does_not_reject_distinct_text():
    assert not _near_duplicate("steel energy costs increased", [])