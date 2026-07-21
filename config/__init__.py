from functools import lru_cache

from .settings import Settings


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
