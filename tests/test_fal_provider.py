"""fal.ai image + any-llm subscribe payload tests (no network)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.fal import FalProvider
from app.services.fal_image import generate_image_bytes_fal


def test_fal_llm_subscribe_payload():
    provider = FalProvider()
    provider.model_name = "google/gemini-2.5-flash"
    fake = {"output": "hello from fal"}
    with patch("app.services.ai.fal.subscribe", return_value=fake) as sub:
        text = provider.generate("you are a stylist", "hi", json_mode=True, max_tokens=100)
    assert text == "hello from fal"
    args = sub.call_args.args
    assert args[0] == "fal-ai/any-llm"
    payload = args[1]
    assert payload["prompt"] == "hi"
    assert payload["model"] == "google/gemini-2.5-flash"
    assert "Return ONLY valid JSON" in payload["system_prompt"]
    assert payload["max_tokens"] == 100


def test_fal_chat_model_ainvoke_shape():
    from langchain_core.messages import HumanMessage, SystemMessage

    provider = FalProvider()
    model = provider.chat_model(temperature=0.2, max_tokens=50)
    with patch.object(provider, "agenerate", new=AsyncMock(return_value="ok")) as gen:
        import asyncio

        result = asyncio.run(model.ainvoke([SystemMessage(content="sys"), HumanMessage(content="user")]))
    assert result.content == "ok"
    gen.assert_awaited()


def test_seedream_edit_sends_image_urls():
    fal_result = {"images": [{"url": "https://fal.media/out.png"}]}
    with (
        patch("app.services.fal_image.subscribe", return_value=fal_result) as sub,
        patch("app.services.fal_image.httpx.Client") as client_cls,
        patch("app.services.fal_image.settings") as s,
    ):
        s.FAL_IMAGE_MODEL = "fal-ai/bytedance/seedream/v5/lite/edit"
        s.FAL_IMAGE_T2I_MODEL = "fal-ai/bytedance/seedream/v5/lite/text-to-image"
        resp = MagicMock()
        resp.content = b"png-bytes"
        resp.raise_for_status = MagicMock()
        client_cls.return_value.__enter__.return_value.get.return_value = resp
        out = generate_image_bytes_fal(
            "groom in sherwani",
            image_urls=["https://example.com/product.jpg", "https://example.com/fabric.jpg"],
        )
    assert out == b"png-bytes"
    model_id, arguments = sub.call_args.args[:2]
    assert model_id == "fal-ai/bytedance/seedream/v5/lite/edit"
    assert arguments["image_urls"] == [
        "https://example.com/product.jpg",
        "https://example.com/fabric.jpg",
    ]
    assert arguments["prompt"] == "groom in sherwani"


def test_seedream_t2i_when_no_fabric():
    fal_result = {"images": [{"url": "https://fal.media/out.png"}]}
    with (
        patch("app.services.fal_image.subscribe", return_value=fal_result) as sub,
        patch("app.services.fal_image.httpx.Client") as client_cls,
        patch("app.services.fal_image.settings") as s,
    ):
        s.FAL_IMAGE_MODEL = "fal-ai/bytedance/seedream/v5/lite/edit"
        s.FAL_IMAGE_T2I_MODEL = "fal-ai/bytedance/seedream/v5/lite/text-to-image"
        resp = MagicMock()
        resp.content = b"png-bytes"
        resp.raise_for_status = MagicMock()
        client_cls.return_value.__enter__.return_value.get.return_value = resp
        generate_image_bytes_fal("groom in sherwani", image_urls=[])
    model_id, arguments = sub.call_args.args[:2]
    assert model_id == "fal-ai/bytedance/seedream/v5/lite/text-to-image"
    assert "image_urls" not in arguments


def test_fal_key_missing_raises():
    with (
        patch("app.services.fal_runtime.settings") as s,
        patch.dict("os.environ", {"FAL_KEY": ""}, clear=False),
    ):
        s.FAL_KEY = ""
        from app.services.fal_runtime import _ensure_fal_key

        with pytest.raises(RuntimeError, match="FAL_KEY"):
            _ensure_fal_key()
