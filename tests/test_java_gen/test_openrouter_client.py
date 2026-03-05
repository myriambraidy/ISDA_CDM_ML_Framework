"""Unit tests for OpenRouterClient with mocked HTTP."""

import unittest
from unittest.mock import patch, MagicMock

from fpml_cdm.java_gen.openrouter_client import (
    OpenRouterClient,
    ChatResponse,
    Choice,
    Message,
    ToolCall,
    FunctionCall,
)


class OpenRouterClientTests(unittest.TestCase):

    def test_create_returns_text_response_structure(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "gen-1",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello world",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("fpml_cdm.java_gen.openrouter_client.requests.post", return_value=mock_response):
            client = OpenRouterClient(api_key="sk-test")
            response = client.chat.completions.create(
                model="test/model",
                messages=[{"role": "user", "content": "Hi"}],
            )

        self.assertIsInstance(response, ChatResponse)
        self.assertEqual(len(response.choices), 1)
        message = response.choices[0].message
        self.assertEqual(message.role, "assistant")
        self.assertEqual(message.content, "Hello world")
        self.assertIsNone(message.tool_calls)

    def test_create_returns_tool_calls_structure(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "gen-2",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "function": {
                                    "name": "inspect_cdm_json",
                                    "arguments": '{"json_path": "/tmp/cdm.json"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("fpml_cdm.java_gen.openrouter_client.requests.post", return_value=mock_response):
            client = OpenRouterClient(api_key="sk-test")
            response = client.chat.completions.create(
                model="test/model",
                messages=[{"role": "user", "content": "Inspect the file"}],
                tools=[{"type": "function", "function": {"name": "inspect_cdm_json", "parameters": {}}}],
                tool_choice="auto",
            )

        message = response.choices[0].message
        self.assertIsNotNone(message.tool_calls)
        self.assertEqual(len(message.tool_calls), 1)
        tc = message.tool_calls[0]
        self.assertEqual(tc.id, "call_abc123")
        self.assertEqual(tc.function.name, "inspect_cdm_json")
        self.assertEqual(tc.function.arguments, '{"json_path": "/tmp/cdm.json"}')

    def test_create_sends_correct_request(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}
        mock_response.raise_for_status = MagicMock()

        with patch("fpml_cdm.java_gen.openrouter_client.requests.post", return_value=mock_response) as post:
            client = OpenRouterClient(api_key="sk-secret")
            client.chat.completions.create(
                model="z-ai/glm-4.6",
                messages=[{"role": "user", "content": "Hi"}],
            )
            post.assert_called_once()
            call_kw = post.call_args[1]
            self.assertEqual(call_kw["headers"]["Authorization"], "Bearer sk-secret")
            self.assertEqual(call_kw["headers"]["Content-Type"], "application/json")
            self.assertIn("https://openrouter.ai/api/v1/chat/completions", post.call_args[0][0])
            self.assertEqual(call_kw["json"]["model"], "z-ai/glm-4.6")
            self.assertEqual(call_kw["json"]["messages"], [{"role": "user", "content": "Hi"}])

    def test_client_requires_api_key(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            OpenRouterClient(api_key="")
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))
        with self.assertRaises(ValueError):
            OpenRouterClient(api_key=None)  # type: ignore[arg-type]
