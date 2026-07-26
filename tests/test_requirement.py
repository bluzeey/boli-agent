from app.integrations.sarvam import heuristic_extract_requirement


def test_requirement_ready_when_need_and_location_present() -> None:
    result = heuristic_extract_requirement("Find pest control vendors in Jaipur")
    assert result.search_ready is True
    assert result.location == "Jaipur"
    assert "pest control" in result.search_query.lower()


def test_short_followup_fills_missing_location() -> None:
    first = heuristic_extract_requirement("Find a commercial pest control company")
    second = heuristic_extract_requirement(
        "Jaipur",
        {
            "normalized_need": first.normalized_need,
            "location": None,
            "must_haves": [],
        },
    )
    assert second.search_ready is True
    assert second.location == "Jaipur"
