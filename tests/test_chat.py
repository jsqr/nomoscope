import json

import httpx
import pytest

from nomoscope import chat
from nomoscope.providers import MISTRAL

MODEL = chat.ChatModel("test-mistral", MISTRAL, "test/chat", 1e-6, 4e-6)
SCHEMA = {"type": "object"}


def client(content) -> httpx.Client:
    def reply(request: httpx.Request) -> httpx.Response:
        body = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }
        return httpx.Response(200, json=body)

    return httpx.Client(base_url=MISTRAL.base_url, transport=httpx.MockTransport(reply))


def test_a_string_reply_is_the_content():
    got = chat.complete(client('{"a": 1}'), MODEL, [{"role": "user", "content": "q"}], SCHEMA)
    assert json.loads(got.content) == {"a": 1}
    assert (got.input_tokens, got.output_tokens) == (10, 3)


def test_a_chunked_reply_keeps_the_text_and_drops_the_thinking():
    chunks = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "hmm"}]},
        {"type": "text", "text": '{"a":'},
        {"type": "text", "text": " 1}"},
    ]
    got = chat.complete(client(chunks), MODEL, [{"role": "user", "content": "q"}], SCHEMA)
    assert json.loads(got.content) == {"a": 1}


def test_a_reply_of_only_thinking_is_empty():
    chunks = [{"type": "thinking", "thinking": [{"type": "text", "text": "hmm"}]}]
    with pytest.raises(chat.ChatError, match="empty reply"):
        chat.complete(client(chunks), MODEL, [{"role": "user", "content": "q"}], SCHEMA)


def test_the_mistral_provider_loads_from_toml(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '["m"]\nprovider = "mistral"\nname = "zai-glm-5-3-flash"\n'
        "dollars_per_input_token = 1.5e-7\ndollars_per_output_token = 5e-7\n"
    )
    assert chat.load(path)["m"].provider is MISTRAL
