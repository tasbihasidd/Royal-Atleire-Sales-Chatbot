"""LangSmith cost ledger + fal usage parsing (no network)."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.services.ai.fal import FalChatModel, FalProvider
from app.services.ai.fal_usage import extract_usage, parse_fal_llm_result
from app.services.cost_tracking import (
    clear_session_cost,
    get_session_cost,
    is_image_like_label,
    record_image_usage,
    record_llm_usage,
    session_cost_scope,
)


def test_parse_fal_usage_from_payload():
    text, pt, ct, estimated = parse_fal_llm_result(
        {
            "output": "ok",
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        },
        system="s",
        user="u",
    )
    assert text == "ok"
    assert pt == 12 and ct == 4 and estimated is False


def test_parse_fal_usage_estimate_fallback():
    text, pt, ct, estimated = parse_fal_llm_result(
        {"output": "abcdefgh"},
        system="sys",
        user="hello",
    )
    assert text == "abcdefgh"
    assert estimated is True
    assert pt >= 1 and ct >= 1


def test_extract_usage_aliases():
    usage = extract_usage({"usage": {"input_tokens": 3, "output_tokens": 7}})
    assert usage == {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}


def test_image_label_detection():
    assert is_image_like_label("seedream-edit", "fal-ai/bytedance/seedream")
    assert is_image_like_label("virtual-tryon", "bria/fibo-edit-1.5/virtual-try-on")
    assert not is_image_like_label("any-llm", "fal-ai/any-llm")
    assert not is_image_like_label("any-llm/vision", "fal-ai/any-llm/vision")


def test_session_cost_ledger():
    clear_session_cost("cost-test")
    with session_cost_scope("cost-test"):
        record_llm_usage(prompt_tokens=1000, completion_tokens=0, model="m1")
        record_image_usage(label="seedream-t2i", model_id="fal-ai/x")
    cost = get_session_cost("cost-test")
    assert cost["llm_calls"] == 1
    assert cost["image_calls"] == 1
    assert cost["prompt_tokens"] == 1000
    assert cost["estimated_usd"] > 0
    clear_session_cost("cost-test")


def test_fal_chat_model_to_chat_result(monkeypatch):
    provider = FalProvider()
    model = FalChatModel(provider, temperature=0.1, max_tokens=64, model_name="test-model")

    monkeypatch.setattr(
        "app.services.ai.fal.subscribe",
        lambda *a, **k: {"output": '{"intent":"general"}', "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    )

    clear_session_cost("fal-chat")
    with session_cost_scope("fal-chat"):
        result = model._generate(
            [SystemMessage(content="sys"), HumanMessage(content="hi")],
        )
    msg = result.generations[0].message
    assert isinstance(msg, AIMessage)
    assert "intent" in str(msg.content)
    assert msg.usage_metadata["input_tokens"] == 5
    assert msg.usage_metadata["output_tokens"] == 2
    cost = get_session_cost("fal-chat")
    assert cost["llm_calls"] == 1
    clear_session_cost("fal-chat")
