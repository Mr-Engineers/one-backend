from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase_client() -> Client:
    """Return a cached Supabase client.

    Requires SUPABASE_URL and SUPABASE_KEY to be set.
    """
    settings = get_settings()
    if not settings.supabase_configured:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY."
        )
    return create_client(settings.supabase_url, settings.supabase_key)
