import json

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
