from __future__ import annotations

import os


class GeminiProvider:
    """LLM provider backed by Google Gemini via google-generativeai."""

    def __init__(self, model: str = "gemini-2.5-flash") -> None:
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is required for the Gemini provider. "
                "Install it with: pip install google-generativeai>=0.8.0"
            ) from exc

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Obtain an API key from https://aistudio.google.com/."
            )
        genai.configure(api_key=api_key)
        self._client = genai.GenerativeModel(self.model)
        return self._client

    def complete(self, prompt: str) -> str:
        client = self._get_client()
        response = client.generate_content(prompt)
        return response.text or ""
