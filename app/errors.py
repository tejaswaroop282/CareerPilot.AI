"""Plain-language messages for run failures that users can act on."""

QUOTA_MESSAGE = "The AI service's usage limit was reached. Please try again later (limits reset daily)."


def friendly_error(e: Exception, limit: int = 500) -> str:
    names = {c.__name__ for c in type(e).__mro__}
    if "RateLimitError" in names or "rate limit" in str(e)[:400].lower() or "429" in str(e)[:120]:
        return QUOTA_MESSAGE
    return str(e)[:limit]
