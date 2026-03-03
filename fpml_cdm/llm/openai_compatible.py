from __future__ import annotations


class OpenAICompatProvider:
    """LLM provider that POSTs to any OpenAI-compatible /chat/completions endpoint.

    Works with Ollama, LM Studio, and any OpenAI-compatible local or remote server.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "llama3.2",
        api_key: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str) -> str:
        try:
            import requests
        except ImportError as exc:
            raise ImportError(
                "requests is required for the openai_compat provider. "
                "Install it with: pip install requests>=2.31.0"
            ) from exc

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
        }

        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
