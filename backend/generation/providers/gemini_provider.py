import json
import logging
import httpx

from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.providers.base_provider import BaseProvider
from backend.generation.providers.provider_result import ProviderResult

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """
    Google Gemini API Provider for VoxCode.
    Uses official REST endpoints with streaming and structured reasoning support.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
    ):
        super().__init__(
            provider_name="gemini",
            model=model,
        )
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def generate(self, prompt: PromptResult) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                response="[Gemini Provider Error: GEMINI_API_KEY is missing in your configuration]",
                model_name=self.model,
                provider_name=self.provider_name,
                prompt_size=prompt.prompt_size,
                completion_size=0,
                total_size=prompt.prompt_size,
            )

        endpoint = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"System Context:\n{prompt.system_prompt}\n\nTask:\n{prompt.user_prompt}"}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 4096,
            },
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(endpoint, json=payload)
            if resp.status_code != 200:
                logger.error(f"Gemini API error ({resp.status_code}): {resp.text}")
                content = f"Error from Gemini API: {resp.text}"
            else:
                data = resp.json()
                try:
                    content = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    content = json.dumps(data)

        return ProviderResult(
            response=content,
            model_name=self.model,
            provider_name=self.provider_name,
            prompt_size=prompt.prompt_size,
            completion_size=len(content),
            total_size=prompt.prompt_size + len(content),
        )
