from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    json_schema: Dict[str, Any]
    handler: Callable[..., Dict[str, Any]]


class ToolRegistry:
    """
    Minimal tool registry that:
      - produces OpenAI-compatible `tools=[...]` payloads for function calling
      - dispatches `tool_name + args` to the registered handler
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def tool_definitions_for_llm(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.json_schema,
                },
            }
            for t in self._tools.values()
        ]

    def dispatch(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return spec.handler(**args)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    @property
    def tools(self) -> Dict[str, ToolSpec]:
        return dict(self._tools)

