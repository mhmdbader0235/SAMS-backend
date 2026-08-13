"""
OPA (Open Policy Agent) Authorization Client for SchoolDesk (Doumind).

This module provides async authorization checks by sending structured input payloads
to the OPA policy decision endpoint (default: http://opa:8181/v1/data/school/authz/allow).
"""

import logging
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import OPA_URL

logger = logging.getLogger("sams.opa")


async def verify_opa_authorization(
    user_id: str,
    tenant_id: str,
    roles: List[str],
    action: str,
    resource: Optional[Dict[str, Any]] = None,
    opa_url: Optional[str] = None,
) -> bool:
    """
    Query OPA policy endpoint to verify if an action on a resource is allowed.

    Input payload is structured as:
    {
      "input": {
        "user": { "id": user_id, "tenant_id": tenant_id, "roles": roles },
        "action": action,
        "resource": resource or {}
      }
    }
    """
    url = opa_url or OPA_URL
    resource_payload = resource or {}

    payload = {
        "input": {
            "user": {
                "id": user_id,
                "tenant_id": tenant_id,
                "roles": roles,
            },
            "action": action,
            "resource": resource_payload,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                result = response.json()
                allowed = result.get("result", False)
                return bool(allowed)
            else:
                logger.warning(
                    f"OPA endpoint returned status {response.status_code}: {response.text}"
                )
                return False
    except Exception as exc:
        logger.error(f"Error connecting to OPA endpoint at {url}: {exc}")
        # Local fallback: super_admin is always allowed
        if "super_admin" in roles:
            return True
        return False


async def verify_opa_http_route(
    user_id: str,
    tenant_id: str,
    roles: List[str],
    method: str,
    path: str,
    opa_url: Optional[str] = None,
) -> bool:
    """
    Query OPA policy endpoint to verify if an HTTP route request is allowed.

    Input payload is structured as:
    {
      "input": {
        "user": { "id": user_id, "tenant_id": tenant_id, "roles": roles },
        "http": { "method": method, "path": path }
      }
    }
    """
    url = opa_url or OPA_URL

    payload = {
        "input": {
            "user": {
                "id": user_id,
                "tenant_id": tenant_id,
                "roles": roles,
            },
            "http": {
                "method": method,
                "path": path,
            },
        }
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                result = response.json()
                allowed = result.get("result", False)
                return bool(allowed)
            else:
                logger.warning(
                    f"OPA endpoint returned status {response.status_code}: {response.text}"
                )
                return False
    except Exception as exc:
        logger.error(f"Error connecting to OPA endpoint at {url}: {exc}")
        if "super_admin" in roles:
            return True
        return False

