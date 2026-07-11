from functools import lru_cache

from agent_framework.openai import OpenAIChatClient

from app.config import get_settings


@lru_cache
def get_chat_client() -> OpenAIChatClient:
    """Build the single Azure OpenAI chat client shared by all agents.

    The Foundry resource's Azure-OpenAI-compatible endpoint may be given either
    as a bare resource root (``https://<resource>.openai.azure.com``) or as a
    full ``.../openai/v1`` base URL. ``OpenAIChatClient`` wants the former via
    ``azure_endpoint`` (it appends the versioned path itself) and the latter via
    ``base_url`` (passed through as-is) -- passing a full ``/openai/v1`` URL as
    ``azure_endpoint`` would double up the path.
    """
    settings = get_settings()
    endpoint = settings.azure_openai_endpoint.rstrip("/")

    endpoint_kwargs: dict[str, str] = (
        {"base_url": endpoint} if endpoint.endswith("/openai/v1") else {"azure_endpoint": endpoint}
    )

    return OpenAIChatClient(
        model=settings.azure_openai_chat_deployment_name,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        **endpoint_kwargs,
    )
