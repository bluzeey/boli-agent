import json
import traceback

import httpx
import pytest

from app.config import Settings
from app.integrations.sarvam import (
    SarvamClient,
    heuristic_extract_quote,
    heuristic_extract_requirement,
)


@pytest.mark.parametrize("kind", ["requirement", "quote"])
def test_structured_extraction_disables_reasoning_and_keeps_schema(kind):
    requests = []
    expected = (
        heuristic_extract_requirement("Find boxes in Jaipur")
        if kind == "requirement"
        else heuristic_extract_quote("Rs 100, delivery 3 days", ["price", "lead_time"])
    )

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(expected.model_dump()),
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = SarvamClient(Settings(sarvam_api_key="synthetic-key"), http)
        actual = (
            client.extract_requirement("Find boxes in Jaipur")
            if kind == "requirement"
            else client.extract_quote("Rs 100, delivery 3 days", ["price", "lead_time"])
        )
    assert actual == expected
    assert len(requests) == 1
    assert requests[0]["reasoning_effort"] is None
    assert requests[0]["max_tokens"] == 2048
    assert requests[0]["response_format"]["type"] == "json_schema"
    contract = requests[0]["response_format"]["json_schema"]
    assert contract["strict"] is True
    assert contract["schema"]["additionalProperties"] is False
    assert set(contract["schema"]["required"]) == set(contract["schema"]["properties"])


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"choices": []},
        {"choices": [None]},
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning_content": "PRIVATE MODEL OUTPUT"},
                }
            ]
        },
        {"choices": [{"message": {"content": "PRIVATE MODEL OUTPUT"}}]},
        {"choices": [{"message": {"content": "[]"}}]},
    ],
)
def test_invalid_or_reasoning_only_answers_fail_without_leaking_output(payload):
    with pytest.raises(RuntimeError) as error:
        SarvamClient._completion_json(httpx.Response(200, json=payload))
    assert "PRIVATE MODEL OUTPUT" not in str(error.value)
    assert "NoneType" not in str(error.value)


@pytest.fixture
def structured_answer(monkeypatch):
    def forbidden_fallback(*args, **kwargs):
        pytest.fail("A configured model must not fall back to heuristic extraction")

    monkeypatch.setattr(
        "app.integrations.sarvam.heuristic_extract_requirement", forbidden_fallback
    )
    monkeypatch.setattr("app.integrations.sarvam.heuristic_extract_quote", forbidden_fallback)

    def extract(kind, content, *, finish_reason="stop", existing_case=None):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if kind == "requirement":
                assert json.dumps(existing_case or {}, ensure_ascii=False) in (
                    body["messages"][1]["content"]
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": finish_reason, "message": {"content": content}}
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = SarvamClient(Settings(sarvam_api_key="synthetic-key"), http)
            try:
                if kind == "requirement":
                    return client.extract_requirement("Find boxes in Jaipur", existing_case)
                return client.extract_quote("Delivery in 3 days", ["price", "lead_time"])
            finally:
                assert len(requests) == 1, "Extraction must not automatically retry paid requests"

    return extract


def test_known_need_and_geography_are_ready_without_optional_facts(structured_answer):
    result = structured_answer(
        "requirement",
        json.dumps({
            "normalized_need": "packaging boxes",
            "location": "Jaipur",
            "search_ready": False,
            "missing_fields": ["budget", "quantity", "deadline", "company_context"],
            "search_query": None,
            "clarifying_question": "What quantity and budget?",
            "acknowledgement": "Captured.",
        }),
    )
    assert result.search_ready is True
    assert result.search_query == "packaging boxes in Jaipur"
    assert result.missing_fields == []
    assert result.clarifying_question is None
    assert result.quantity is result.budget is result.deadline is result.company_context is None
    assert result.must_haves == []


@pytest.mark.parametrize("location", ["India", "remote"])
def test_explicit_national_or_remote_geography_is_ready(structured_answer, location):
    result = structured_answer(
        "requirement",
        json.dumps({
            "normalized_need": "translation service",
            "location": location,
            "acknowledgement": "Captured.",
        }),
    )
    assert result.search_ready is True
    assert result.search_query == f"translation service in {location}"


@pytest.mark.parametrize(
    ("need", "location", "missing"),
    [
        ("packaging boxes", None, ["location"]),
        ("packaging boxes", " \t", ["location"]),
        (" \n", "Jaipur", ["normalized_need"]),
        ("", None, ["normalized_need", "location"]),
    ],
)
def test_missing_need_or_geography_cannot_search(structured_answer, need, location, missing):
    result = structured_answer(
        "requirement",
        json.dumps({
            "normalized_need": need,
            "location": location,
            "search_ready": True,
            "search_query": "invented query in invented city",
            "missing_fields": [],
            "acknowledgement": "Captured.",
        }),
    )
    assert result.search_ready is False
    assert result.search_query is None
    assert result.missing_fields == missing
    if not location or not location.strip():
        assert result.location is None
    else:
        assert result.location == location


def test_existing_case_context_and_stated_commercial_facts_are_preserved(structured_answer):
    existing = {
        "normalized_need": "corrugated boxes",
        "quantity": "250 pieces",
        "budget": "INR 5,000",
        "deadline": "next Friday",
        "company_context": "Asha Bakery",
        "must_haves": ["food-safe"],
    }
    original = json.loads(json.dumps(existing))
    result = structured_answer(
        "requirement",
        json.dumps({**existing, "location": "Jaipur", "acknowledgement": "Captured."}),
        existing_case=existing,
    )
    for field, value in existing.items():
        assert getattr(result, field) == value
    assert existing == original
    assert result.search_ready is True


def test_blank_optional_requirement_values_remain_unknown(structured_answer):
    result = structured_answer(
        "requirement",
        json.dumps({
            "normalized_need": "boxes",
            "location": "Jaipur",
            "quantity": " ",
            "budget": "\t",
            "deadline": "\n",
            "company_context": "",
            "acknowledgement": "Captured.",
        }),
    )
    assert result.quantity is result.budget is result.deadline is result.company_context is None
    assert result.search_ready is True


def test_quote_leaves_unstated_price_and_terms_unknown(structured_answer):
    result = structured_answer(
        "quote", json.dumps({"lead_time": "3 days", "missing": ["price"]})
    )
    assert result.lead_time == "3 days"
    assert result.price is result.unit_price is result.tax is result.payment_terms is None
    assert result.exclusions == []
    assert result.missing == ["price"]


@pytest.mark.parametrize("fence", [None, "", "json", "JSON"])
def test_complete_json_or_fully_enclosed_markdown_is_accepted(fence):
    content = '{"normalized_need":"बक्से","location":"जयपुर"}'
    if fence is not None:
        content = f"```{fence}\n{content}\n```"
    parsed = SarvamClient._completion_json(
        httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        )
    )
    assert parsed == {"normalized_need": "बक्से", "location": "जयपुर"}


@pytest.mark.parametrize("kind", ["requirement", "quote"])
@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        '{"private":"PRIVATE MODEL OUTPUT"',
        '```json\n{"private":"PRIVATE MODEL OUTPUT"}',
        '{"private":"PRIVATE MODEL OUTPUT"} trailing junk',
        '```json\n{"private":"PRIVATE MODEL OUTPUT"}\n``` trailing junk',
        '[{"private":"PRIVATE MODEL OUTPUT"}]',
        '"PRIVATE MODEL OUTPUT"',
    ],
)
def test_bad_model_json_fails_once_without_fallback_or_output_leak(
    structured_answer, kind, content
):
    with pytest.raises(RuntimeError) as error:
        structured_answer(kind, content)
    assert "PRIVATE MODEL OUTPUT" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("kind", ["requirement", "quote"])
@pytest.mark.parametrize(
    "finish_reason", ["length", "content_filter", "tool_calls", "PRIVATE OUTPUT"]
)
def test_nonstop_completion_is_rejected_even_with_valid_json(
    structured_answer, kind, finish_reason
):
    with pytest.raises(RuntimeError, match="incomplete structured answer") as error:
        structured_answer(kind, "{}", finish_reason=finish_reason)
    assert "PRIVATE OUTPUT" not in str(error.value)


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("requirement", {"search_ready": "false"}),
        ("requirement", {"location": ["PRIVATE MODEL OUTPUT"]}),
        ("quote", {"price": 100}),
    ],
)
def test_wrong_field_types_fail_once_without_coercion(structured_answer, kind, fields):
    content = {"normalized_need": "boxes", "acknowledgement": "Captured.", **fields}
    with pytest.raises(RuntimeError, match="invalid .* fields") as error:
        structured_answer(kind, json.dumps(content))
    assert "PRIVATE MODEL OUTPUT" not in str(error.value)


@pytest.mark.parametrize("kind", ["requirement", "quote"])
def test_invalid_structured_fields_do_not_echo_model_output(kind):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"normalized_need":[],"price":[],'
                                    '"private":"PRIVATE MODEL OUTPUT"}'
                                )
                            }
                        }
                    ],
                },
            )
        )
    ) as http:
        client = SarvamClient(Settings(sarvam_api_key="synthetic-key"), http)
        with pytest.raises(RuntimeError) as error:
            client.extract_requirement(
                "synthetic"
            ) if kind == "requirement" else client.extract_quote("synthetic", [])
    assert "PRIVATE MODEL OUTPUT" not in str(error.value)
