"""
Tests for app/chat.py (step 10: text-to-query chatbot).

Uses a mocked Anthropic client -- there is no real ANTHROPIC_API_KEY in
this environment, and even if there were, spending API credits on every
test run isn't appropriate for a unit suite. The mock stands in only for
`anthropic_client.messages.create`; everything downstream of that
(tool_call routing, execute_tool, the FastAPI TestClient hitting the real
endpoint code, the iteration cap, refusal handling) runs for real, so
these tests do exercise the actual backend query path -- they just don't
prove a real Claude response comes back well-formed. That last piece
still needs manual verification with a real key (see README's Known
limitations).
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chat import MAX_TOOL_ITERATIONS, build_label_source_context, execute_tool, run_chat
from tests.conftest import seed_comments


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, tool_input, block_id="tool_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _response(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class ScriptedAnthropicClient:
    """Returns each entry in `responses` in order, one per .create() call;
    raises if called more times than scripted (so a runaway loop fails
    loudly instead of silently looping forever)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedAnthropicClient called more times than scripted")
        return self._responses.pop(0)


@pytest.fixture
def seeded_client(api_client):
    seed_comments(api_client.db_conn, [
        {"id": "c1", "platform": "youtube", "timestamp": "2024-09-15T00:00:00", "final_label": "negative", "label_source": "vader"},
        {"id": "c2", "platform": "youtube", "timestamp": "2024-09-16T00:00:00", "final_label": "positive", "label_source": "vader"},
        {"id": "c3", "platform": "twitter", "timestamp": "2024-09-17T00:00:00", "final_label": "negative", "label_source": "transformer"},
    ])
    return api_client


def test_run_chat_tool_use_then_answer(seeded_client):
    scripted = ScriptedAnthropicClient([
        _response("tool_use", [_tool_use_block("get_summary", {})]),
        _response("end_turn", [_text_block("Overall sentiment is mixed, mostly negative on YouTube.")]),
    ])

    result = run_chat(seeded_client, scripted, seeded_client.db_conn, "What's the overall sentiment?")

    assert result["answer"] == "Overall sentiment is mixed, mostly negative on YouTube."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "get_summary"
    assert "total_comments" in result["tool_calls"][0]["output"]
    assert len(scripted.calls) == 2


def test_run_chat_executes_real_endpoint_not_a_stub(seeded_client):
    """The tool call must route through the real /api/summary logic --
    assert the returned counts match what was actually seeded, not just
    that *some* dict came back."""
    scripted = ScriptedAnthropicClient([
        _response("tool_use", [_tool_use_block("get_summary", {"platform": "youtube"})]),
        _response("end_turn", [_text_block("done")]),
    ])

    result = run_chat(seeded_client, scripted, seeded_client.db_conn, "youtube sentiment?")

    output = result["tool_calls"][0]["output"]
    assert output["total_comments"] == 2  # c1 + c2 only, twitter's c3 excluded by the filter


def test_run_chat_iteration_cap_enforced(seeded_client):
    """If the model keeps calling tools forever, run_chat must stop at
    MAX_TOOL_ITERATIONS and force a final no-tools answer rather than
    looping indefinitely."""
    tool_use_responses = [
        _response("tool_use", [_tool_use_block("get_summary", {}, block_id=f"t{i}")])
        for i in range(MAX_TOOL_ITERATIONS)
    ]
    final_response = _response("end_turn", [_text_block("Here's what I found before running out of turns.")])
    scripted = ScriptedAnthropicClient(tool_use_responses + [final_response])

    result = run_chat(seeded_client, scripted, seeded_client.db_conn, "loop forever please")

    assert result["answer"] == "Here's what I found before running out of turns."
    assert len(result["tool_calls"]) == MAX_TOOL_ITERATIONS
    assert len(scripted.calls) == MAX_TOOL_ITERATIONS + 1
    # the forced final call must disable further tool use
    assert scripted.calls[-1]["tool_choice"] == {"type": "none"}


def test_run_chat_refusal_short_circuits(seeded_client):
    scripted = ScriptedAnthropicClient([
        _response("refusal", []),
    ])

    result = run_chat(seeded_client, scripted, seeded_client.db_conn, "something disallowed")

    assert result["refused"] is True
    assert result["tool_calls"] == []
    assert len(scripted.calls) == 1


def test_execute_tool_unknown_name_returns_error(seeded_client):
    output = execute_tool(seeded_client, "not_a_real_tool", {})
    assert "error" in output


def test_execute_tool_drops_empty_filter_values(seeded_client):
    """tool_input often includes params the model set to '' rather than
    omitting -- these must not be sent as literal empty-string filters
    (which the API endpoints treat as 'match nothing'), since the model
    means 'no filter', not 'exclude everything'."""
    output = execute_tool(seeded_client, "get_summary", {"platform": "", "sentiment": None})
    assert output["total_comments"] == 3  # all three seeded rows, not zero


def test_build_label_source_context_reports_vader_and_transformer_mix(seeded_client):
    # seeded_client fixture has 2 vader-labeled rows (c1, c2) and 1 transformer-labeled row (c3).
    # build_label_source_context resolves data/classification_report.json relative to the real
    # project root (not a fixture path), so this test only asserts on the part driven by the
    # rows *this test* seeded -- it doesn't assume anything about whether the repo's real gold
    # sample has been completed yet (see pipeline/label_gold.py), since that's outside this
    # test's control and will legitimately change once the user labels one.
    context = build_label_source_context(seeded_client.db_conn)
    assert "Of 3 classified comments" in context
    assert "1 are labeled by the HuggingFace" in context
    assert "2 by the VADER" in context


def test_api_chat_returns_503_without_api_key(api_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = api_client.post("/api/chat", json={"question": "anything"})
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_api_chat_rejects_empty_question(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    response = api_client.post("/api/chat", json={"question": "   "})
    assert response.status_code == 400
