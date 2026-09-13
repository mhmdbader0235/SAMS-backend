"""
Unit tests for membership caching.
"""

from app.core.dependencies import (
    _MEMBERSHIP_CACHE,
    invalidate_membership_cache,
)


def test_membership_cache_invalidation():
    _MEMBERSHIP_CACHE["test@school.com"] = (100.0, [{"tenant_id": "tenant_a", "role": "teacher"}])
    _MEMBERSHIP_CACHE["other@school.com"] = (100.0, [{"tenant_id": "tenant_b", "role": "parent"}])

    invalidate_membership_cache("test@school.com")
    assert "test@school.com" not in _MEMBERSHIP_CACHE
    assert "other@school.com" in _MEMBERSHIP_CACHE

    invalidate_membership_cache()
    assert len(_MEMBERSHIP_CACHE) == 0
