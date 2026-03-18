"""Lightweight OpenRouter HTTP client (OpenAI-compatible API shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import requests


@dataclass
class FunctionCall:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    function: FunctionCall


@dataclass
class Message:
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None


@dataclass
class Choice:
    message: Message
    index: int = 0
    finish_reason: Optional[str] = None


@dataclass
class ChatResponse:
    choices: List[Choice]
    id: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Any] = field(default=None)


def _message_to_dict(msg: Any) -> dict:
    if isinstance(msg, dict):
        return msg
    assert isinstance(msg, Message)
    out: dict = {"role": msg.role, "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return out


class _Completions:
    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def create(
        self,
        model: str,
        messages: List[Any],
        tools: Optional[List[dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> ChatResponse:
        payload: dict = {
            "model": model,
            "messages": [_message_to_dict(m) for m in messages],
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = []
        for i, c in enumerate(data.get("choices", [])):
            msg = c.get("message", {})
            tool_calls = None
            if msg.get("tool_calls"):
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", ""),
                        function=FunctionCall(
                            name=tc.get("function", {}).get("name", ""),
                            arguments=tc.get("function", {}).get("arguments", ""),
                        ),
                    )
                    for tc in msg["tool_calls"]
                ]
            choices.append(
                Choice(
                    message=Message(
                        role=msg.get("role", "assistant"),
                        content=msg.get("content") or None,
                        tool_calls=tool_calls,
                    ),
                    index=i,
                    finish_reason=c.get("finish_reason"),
                )
            )
        return ChatResponse(
            choices=choices,
            id=data.get("id"),
            model=data.get("model"),
            usage=data.get("usage"),
        )


class _Chat:
    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._completions = _Completions(api_key, base_url, timeout)

    @property
    def completions(self) -> _Completions:
        return self._completions


class OpenRouterClient:
    """OpenRouter HTTP client with OpenAI-style chat.completions.create()."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter API key is required (OPENROUTER_API_KEY env)")
        self._chat = _Chat(api_key, base_url, timeout)

    @property
    def chat(self) -> _Chat:
        return self._chat
