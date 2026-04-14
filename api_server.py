#!/usr/bin/env python3
"""
Shareable API for Centene Kentucky location fetch.
"""

import base64
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

load_dotenv()

BASE_URL = "https://prod.api.centene.com/fhir/providerdirectory"


class FetchResponse(BaseModel):
    run_at: str = Field(description="ISO timestamp when fetch ran")
    total_ky_locations: int = Field(description="Count of Kentucky locations returned")
    sources: List[str] = Field(description="Unique meta.source values from entries")
    entries: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Raw FHIR bundle entries (only included when include_entries=true)",
    )


app = FastAPI(
    title="Centene KY Locations API",
    description="Fetches Kentucky Location resources from Centene Provider Directory.",
    version="1.0.0",
)


def _auth_header() -> str:
    user = os.getenv("CENTENE_USER")
    password = os.getenv("CENTENE_PASS")
    if not user or not password:
        raise HTTPException(
            status_code=500,
            detail="Missing CENTENE_USER or CENTENE_PASS in environment variables.",
        )
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _get(url: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        response = requests.get(url, headers=headers, params=params, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Network error while calling Centene API: {exc}") from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Centene API returned {response.status_code}: {response.text[:200]}",
        )
    return response.json()


def _fetch_all_ky_locations(count_per_page: int = 200, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
    headers = {"Authorization": _auth_header(), "Accept": "application/fhir+json"}
    entries: List[Dict[str, Any]] = []
    current_url: Optional[str] = f"{BASE_URL}/Location"
    current_params: Optional[Dict[str, Any]] = {"address-state": "KY", "_count": count_per_page}

    page = 1
    while current_url:
        body = _get(current_url, headers=headers, params=current_params)
        batch = body.get("entry", [])
        entries.extend(batch)
        if max_pages is not None and page >= max_pages:
            break

        next_url = None
        for link in body.get("link", []):
            if link.get("relation") == "next":
                next_url = link.get("url")
                break

        current_url = next_url
        current_params = None
        page += 1

    return entries


@app.get("/", tags=["Health"])
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/centene/ky-locations",
    response_model=FetchResponse,
    tags=["Centene"],
    summary="Fetch Kentucky locations",
)
def fetch_ky_locations(
    include_entries: bool = Query(
        default=False,
        description="When true, include full raw entries in response.",
    ),
    max_pages: Optional[int] = Query(
        default=None,
        ge=1,
        description="Optional page limit for faster testing (omit for full fetch).",
    ),
) -> FetchResponse:
    entries = _fetch_all_ky_locations(max_pages=max_pages)
    sources = sorted(
        {
            entry.get("resource", {}).get("meta", {}).get("source", "?")
            for entry in entries
        }
    )
    return FetchResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        total_ky_locations=len(entries),
        sources=sources,
        entries=entries if include_entries else None,
    )
