"""
API Gateway — transparent reverse proxy for the SchoolDesk backend.

Sits in front of the FastAPI backend service and forwards all /api/v1/*
requests. Handles CORS in one place so backend service does not need to.
BACKEND_URL is read from the environment, defaulting to localhost for dev.
"""

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

app = FastAPI(title="SchoolDesk API Gateway", version="1.0.0")

# ─── CORS — single source of truth ───────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Read backend URL from environment so Docker Compose can inject the service name
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")


# ─── Proxy handler ────────────────────────────────────────────────────────────
@app.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(request: Request, path: str) -> Response:
    """Forward every /api/v1/* request to the backend service."""
    target_url = f"{BACKEND_URL}/api/v1/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    body = await request.body()
    # Drop the host header so the backend receives its own host
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            follow_redirects=True,
        )

    # Drop hop-by-hop headers that must not be forwarded
    excluded = {"content-length", "transfer-encoding", "connection"}
    forwarded_headers = {
        k: v for k, v in response.headers.items() if k.lower() not in excluded
    }

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=forwarded_headers,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
