from app.integrations.sarvam import heuristic_extract_quote

REQUIRED = ["price", "tax", "lead_time", "payment_terms"]


def test_heuristic_parses_complete_quote():
    q = heuristic_extract_quote(
        "Our quote: Rs 5000, GST 18%, delivery in 3 days. "
        "Payment: 50% advance, 50% on delivery. Excludes installation.",
        REQUIRED,
    )
    assert q.price == "5000"
    assert q.tax == "18%"
    assert q.lead_time == "3 days"
    assert q.payment_terms is not None
    assert "advance" in q.payment_terms
    assert q.exclusions and "installation" in q.exclusions[0]
    assert q.missing == []


def test_heuristic_computes_missing_fields():
    q = heuristic_extract_quote("Rs 3000 only", REQUIRED)
    assert q.price == "3000"
    assert q.tax is None
    assert q.lead_time is None
    assert q.payment_terms is None
    assert q.missing == ["tax", "lead_time", "payment_terms"]


def test_heuristic_parses_unit_price():
    q = heuristic_extract_quote("Rs 500 per unit, delivery 2 weeks", REQUIRED)
    assert q.unit_price == "500"
    assert q.lead_time == "2 weeks"


def test_heuristic_handles_nothing_recognisable():
    q = heuristic_extract_quote("Hello, thanks for the enquiry.", REQUIRED)
    assert q.price is None
    assert q.missing == REQUIRED
