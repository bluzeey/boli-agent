from app.schemas import SearchResult
from app.services.formatting import render_search_results


def test_search_results_are_whatsapp_friendly() -> None:
    output = render_search_results(
        "pest control in Jaipur",
        [
            SearchResult(
                external_id="1",
                name="Example Vendor",
                address="C-Scheme, Jaipur",
                phone="+91 99999 99999",
                rating=4.5,
                review_count=40,
                source_url="https://example.com",
                provider="test",
            )
        ],
    )
    assert "*1. Example Vendor*" in output
    assert "Reply with" in output
