from scripts.lint_summaries import _summary_metrics, discover


FIXTURE = """### Financial Insights
1. **Revenue rose 10% to $110 million.** The company reported sales were up from $100 million.
2. **Management cited energy costs.** The company reported expenses of $50 million due to energy costs.

### Wrap Up
Revenue rose, but energy costs remained a risk.
"""


def test_summary_metrics_capture_quality_signals():
    metrics = _summary_metrics(FIXTURE)

    assert metrics["word_count"] > 0
    assert metrics["item_count"] == 2
    assert metrics["figures_per_item"] == 2.0
    assert metrics["items_with_zero_figures"] == 0
    assert metrics["bold_body_jaccard"] < 1.0
    assert metrics["unattributed_causal_phrases"] == 0
    assert metrics["truncated"] is False


def test_summary_metrics_flag_repetition_and_secondary_padding():
    text = """### Financial Insights
1. **Shipments increased 5% while margins fell.** Shipments rose 5% to 2 million tons.
2. **Energy costs increased to $5 billion.** Natural gas costs increased.

### Operations Insights
3. **Production rose with shipments while energy use increased.** Production rose 5% to 2 million tons.

### Corporate and Risk Insights
4. **A lawsuit remains pending.** The company expects no material impact.

### Wrap Up
Shipments increased 5%, while energy costs increased to $5 billion.
"""
    metrics = _summary_metrics(text)

    assert metrics["repeated_metric_families"]["shipments_and_production"] == 2
    assert metrics["repeated_metric_families"]["energy_and_raw_materials"] == 2
    assert metrics["secondary_section_items"] == 1
    assert metrics["wrap_up_reused_figures"] == 2


def test_discover_reports_reused_phrases_and_issuer_spread():
    result = discover({"NUE 2024 Q1": FIXTURE, "STLD 2024 Q1": FIXTURE})
    assert any(entry["issuer_spread"] == 2 for entry in result["reused_4grams"])