import os
from typing import Optional
from google import genai

from backend.app.core.config import settings


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini client initialization fails due to missing or invalid credentials."""
    pass


def get_gemini_client(api_key: Optional[str] = None) -> genai.Client:
    """Initialize and return the official google-genai SDK client.
    
    Security rule: The API key is never logged, printed, or exposed in error messages.
    """
    if api_key is not None:
        key = api_key
    else:
        key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not key.strip():
        raise GeminiConfigurationError(
            "GEMINI_API_KEY is not configured in environment or settings. "
            "Please provide a valid Gemini API key to initialize the client."
        )

    try:
        client = genai.Client(api_key=key.strip())
        return client
    except Exception as exc:
        raise GeminiConfigurationError(
            f"Failed to initialize google-genai Client: {type(exc).__name__}"
        )
