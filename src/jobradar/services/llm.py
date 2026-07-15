"""Central OpenAI-compatible client factory.

Three modes, in priority order:
  1. Portkey gateway  — if PORTKEY_API_KEY is set.
  2. Custom base_url  — if OPENAI_BASE_URL is set (any OpenAI-compatible endpoint).
  3. Direct OpenAI    — otherwise (OPENAI_API_KEY).

All scoring / embedding / CV code goes through get_client(), so switching providers
is a config change, never a code change.
"""

from openai import OpenAI

from jobradar.config import settings

PORTKEY_GATEWAY_URL = "https://api.portkey.ai/v1"


def get_client() -> OpenAI:
    if not (settings.portkey_api_key or settings.openai_api_key):
        raise RuntimeError(
            "No LLM credentials configured. Set PORTKEY_API_KEY (+ PORTKEY_VIRTUAL_KEY) "
            "or OPENAI_API_KEY in your environment/.env."
        )
    # The OpenAI SDK requires a non-empty api_key string even when auth is via headers.
    api_key = settings.openai_api_key or "not-needed"

    if settings.portkey_api_key:
        headers = {"x-portkey-api-key": settings.portkey_api_key}
        if settings.portkey_virtual_key:
            headers["x-portkey-virtual-key"] = settings.portkey_virtual_key
        return OpenAI(api_key=api_key, base_url=PORTKEY_GATEWAY_URL, default_headers=headers)

    if settings.openai_base_url:
        return OpenAI(api_key=api_key, base_url=settings.openai_base_url)

    return OpenAI(api_key=api_key)
