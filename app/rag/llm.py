"""Model and embeddings factory."""
import os

from langchain_core.exceptions import OutputParserException
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.config import get_settings
from app.secrets import get_secret


def configure_tracing() -> None:
    """Enable LangSmith only when a key is present, so the app runs without one."""
    s = get_settings()
    enabled = s.langsmith_tracing and bool(s.langsmith_api_key)
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if s.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = s.langsmith_project


_override = None  # tests install a scripted model here so graphs run without API calls


def set_chat_model_override(model) -> None:
    global _override
    _override = model


def _openrouter(temperature: float):
    """OpenRouter speaks the OpenAI API; free models are rate limited per day, so retries stay short."""
    from langchain_openai import ChatOpenAI  # noqa: PLC0415

    class OpenRouterChat(ChatOpenAI):
        """Free OpenRouter models differ in JSON-schema support (measured: only some accept it); function calling
        worked on all the ones tested, so structured output always uses it, with a short retry on malformed output."""

        def with_structured_output(self, schema, **kw):
            kw["method"] = "function_calling"
            kw.pop("strict", None)
            runnable = super().with_structured_output(schema, **kw)
            return runnable.with_retry(
                retry_if_exception_type=(OutputParserException, ValueError), stop_after_attempt=3, wait_exponential_jitter=False
            )

    s = get_settings()
    return OpenRouterChat(
        model=s.openrouter_model, api_key=get_secret("OPENROUTER_API_KEY", s.openrouter_api_key), base_url=s.openrouter_base_url,
        temperature=temperature, max_retries=2,
    )


def wait_out_rate_limits(runnable, error_type):
    """Per-minute token limits clear within seconds ("try again in 10s"), so wait and retry before giving up or falling
    back. A daily limit costs about a minute of waiting before the fallback model takes over."""
    return runnable.with_retry(
        retry_if_exception_type=(error_type,), stop_after_attempt=5,
        exponential_jitter_params={"initial": 5.0, "max": 25.0, "jitter": 1.0},
    )


def get_chat_model(temperature: float | None = None, effort: str | None = None):
    """effort: reasoning effort (low|medium|high) for models that support it; low suits plain extraction and saves tokens."""
    if _override is not None:
        return _override
    s = get_settings()
    if s.llm_provider == "groq":
        from langchain_groq import ChatGroq  # noqa: PLC0415

        import groq  # noqa: PLC0415

        class RobustGroq(ChatGroq):
            """JSON-schema structured output (more reliable than tool-calling on gpt-oss), retries on API 400s, and
            automatic fallback to other Groq models (separate daily token budgets) when a rate limit is hit."""

            fallbacks: list = []

            def with_structured_output(self, schema, **kw):
                kw.setdefault("method", "json_schema")
                # a malformed completion (e.g. the model echoes the schema) is retried like an API 400
                retry_on = (groq.BadRequestError, OutputParserException)
                main = super().with_structured_output(schema, **kw).with_retry(retry_if_exception_type=retry_on, stop_after_attempt=3, wait_exponential_jitter=False)
                main = wait_out_rate_limits(main, groq.RateLimitError)
                alts = [f.with_structured_output(schema, **kw) for f in self.fallbacks]
                return main.with_fallbacks(alts, exceptions_to_handle=(groq.RateLimitError,)) if alts else main

            def bind_tools(self, tools, **kw):
                main = wait_out_rate_limits(super().bind_tools(tools, **kw), groq.RateLimitError)
                alts = [f.bind_tools(tools, **kw) for f in self.fallbacks]
                return main.with_fallbacks(alts, exceptions_to_handle=(groq.RateLimitError,)) if alts else main

        temp = s.agent_temperature if temperature is None else temperature

        def make(model: str, fallbacks: list, key: str) -> RobustGroq:
            extra = {"reasoning_effort": effort} if effort else {}
            return RobustGroq(model=model, api_key=key, max_retries=2, temperature=temp, fallbacks=fallbacks, **extra)

        # keys are looked up per call, so a key rotated in Parameter Store is used within a minute (see app/secrets.py)
        keys = [get_secret("GROQ_API_KEY", s.groq_api_key)]
        keys += [k.strip() for k in get_secret("GROQ_API_KEYS", s.groq_api_keys).split(",") if k.strip()]
        keys = list(dict.fromkeys(k for k in keys if k))
        alts = [make(s.groq_model, [], k) for k in keys[1:]]  # other keys: useful only if they belong to another Groq organisation
        alts += [make(m.strip(), [], keys[0]) for m in s.groq_fallback_models.split(",") if m.strip()]
        if get_secret("OPENROUTER_API_KEY", s.openrouter_api_key):  # another provider has its own quota, so it covers a Groq daily-token limit
            alts.append(_openrouter(temp))
        return make(s.groq_model, alts, keys[0] if keys else "")
    if s.llm_provider == "openrouter":
        return _openrouter(s.agent_temperature if temperature is None else temperature)
    return ChatGoogleGenerativeAI(
        model=s.gemini_model,
        google_api_key=s.google_api_key,
        temperature=s.agent_temperature if temperature is None else temperature,
    )


class LocalEmbeddings:
    """Open embedding model run locally with sentence-transformers (no API key, no quota). BGE models expect an
    instruction prefix on queries (not on documents); vectors are normalised for cosine search."""

    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.model = SentenceTransformer(model_name)
        self.is_bge = "bge" in model_name.lower()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()

    def embed_query(self, text: str) -> list[float]:
        q = (self.QUERY_PREFIX + text) if self.is_bge else text
        return self.model.encode([q], normalize_embeddings=True, show_progress_bar=False)[0].tolist()


_local: LocalEmbeddings | None = None


def get_embeddings():
    s = get_settings()
    if s.embedding_provider == "local":
        global _local
        if _local is None:
            _local = LocalEmbeddings(s.local_embedding_model)
        return _local
    return GoogleGenerativeAIEmbeddings(
        model=f"models/{s.gemini_embedding_model}", google_api_key=s.google_api_key
    )


def message_text(msg) -> str:
    """Gemini returns content either as a string or a list of blocks; flatten to plain text."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
