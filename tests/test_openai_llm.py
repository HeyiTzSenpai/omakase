"""Payload-contract tests for OpenAI-compatible providers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from omakase.llm import get_llm
from omakase.llm.openai import OpenAILLM


def _mock_client():
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}

    client = MagicMock()
    client.post.return_value = response

    manager = MagicMock()
    manager.__enter__.return_value = client
    manager.__exit__.return_value = None
    return manager, client


@pytest.mark.parametrize(
    "model,expected_max_tokens",
    [
        ("deepseek-v4-flash", 8192),
        ("deepseek-v4-pro", 8192),
    ],
)
def test_current_deepseek_models_receive_a_complete_json_output_budget(
    model,
    expected_max_tokens,
):
    manager, client = _mock_client()

    with patch("httpx.Client", return_value=manager):
        llm = OpenAILLM("https://api.deepseek.com", model, api_key="request-key")
        llm.generate("Return JSON.", supports_json=True)

    payload = client.post.call_args.kwargs["json"]
    assert payload["max_tokens"] == expected_max_tokens
    assert payload["response_format"] == {"type": "json_object"}


def test_openwebui_uses_its_instance_chat_completions_endpoint():
    manager, client = _mock_client()

    with patch("httpx.Client", return_value=manager):
        llm = get_llm(
            "openwebui",
            "https://models.example.com/team/",
            "llama3.1:8b",
            api_key="openwebui-key",
        )
        llm.generate("Return JSON.", supports_json=False)

    assert client.post.call_args.args[0] == ("https://models.example.com/team/api/chat/completions")
    assert client.post.call_args.kwargs["headers"]["Authorization"] == ("Bearer openwebui-key")
