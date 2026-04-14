#!/usr/bin/env python3
"""
Shareable API for Centene Kentucky location fetch.
"""

import base64
import os
import re
import time
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


class InsurancePlanFilterResponse(BaseModel):
    run_at: str = Field(description="ISO timestamp when fetch ran")
    filter_query: str = Field(description="FHIR query format applied to InsurancePlan search")
    query_mode: str = Field(description="How data was fetched for reliable filtering")
    total_insurance_plans: int = Field(description="Count of InsurancePlan entries returned")
    sources: List[str] = Field(description="Unique meta.source values from entries")
    skipped_sources: List[str] = Field(
        default_factory=list,
        description="Sources skipped due to upstream timeout/error.",
    )
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
    retries = 3
    backoff_seconds = 1.0
    last_response: Optional[requests.Response] = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            last_response = response
        except requests.RequestException as exc:
            if attempt == retries:
                raise HTTPException(status_code=502, detail=f"Network error while calling Centene API: {exc}") from exc
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
            continue

        if response.status_code == 200:
            return response.json()

        if response.status_code in (502, 503, 504) and attempt < retries:
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
            continue

        break

    assert last_response is not None
    raise HTTPException(
        status_code=502,
        detail=f"Centene API returned {last_response.status_code}: {last_response.text[:200]}",
    )


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


def _plan_matches_name_and_type(entry: Dict[str, Any], name_contains: str, plan_type_text: str) -> bool:
    resource = entry.get("resource", {})
    name = (resource.get("name") or "").lower()

    name_token = name_contains.lower()
    if name_token and name_token != "kentucky" and name_token not in name:
        return False

    for plan_type in resource.get("type", []):
        text_value = (plan_type.get("text") or "").lower()
        if plan_type_text.lower() in text_value:
            return True
        for coding in plan_type.get("coding", []):
            display = (coding.get("display") or "").lower()
            code = (coding.get("code") or "").lower()
            if plan_type_text.lower() in display or plan_type_text.lower() in code:
                return True
    return False


def _validate_filter_value(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{field_name} cannot be empty.")
    if len(cleaned) > 80:
        raise HTTPException(status_code=422, detail=f"{field_name} is too long (max 80 characters).")
    if not re.fullmatch(r"[A-Za-z0-9 \-]+", cleaned):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} contains invalid characters. Use letters, numbers, spaces, and hyphens only.",
        )
    return cleaned


def _fetch_insurance_plans_by_source(
    source: str, count_per_page: int = 200, max_pages: Optional[int] = None
) -> List[Dict[str, Any]]:
    headers = {"Authorization": _auth_header(), "Accept": "application/fhir+json"}
    entries: List[Dict[str, Any]] = []
    current_url: Optional[str] = f"{BASE_URL}/InsurancePlan"
    current_params: Optional[Dict[str, Any]] = {"_source": source, "_count": count_per_page}

    page = 1
    while current_url:
        body = _get(current_url, headers=headers, params=current_params)
        entries.extend(body.get("entry", []))
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
        default=1,
        ge=1,
        description="Page limit for reliability in deployment (default 1).",
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


@app.get(
    "/centene/insurance-plans/kentucky-medicaid",
    response_model=InsurancePlanFilterResponse,
    tags=["Centene"],
    summary="Fetch InsurancePlan by Kentucky + Medicaid filter",
)
def fetch_kentucky_medicaid_insurance_plans(
    name_contains: str = Query(
        default="Kentucky",
        alias="name:contains",
        description="InsurancePlan name must contain this value.",
    ),
    plan_type_text: str = Query(
        default="Medicaid",
        alias="plan-type:text",
        description="InsurancePlan plan type text must match this value.",
    ),
    include_entries: bool = Query(
        default=False,
        description="When true, include full raw entries in response.",
    ),
    max_pages: Optional[int] = Query(
        default=1,
        ge=1,
        description="Page limit for reliability in deployment (default 1).",
    ),
) -> InsurancePlanFilterResponse:
    validated_name_contains = _validate_filter_value(name_contains, "name:contains")
    validated_plan_type_text = _validate_filter_value(plan_type_text, "plan-type:text")

    # Reliable strategy: Kentucky scope comes from KY locations, then plans are filtered by type/name.
    ky_location_entries = _fetch_all_ky_locations(max_pages=max_pages)
    ky_sources = {
        entry.get("resource", {}).get("meta", {}).get("source")
        for entry in ky_location_entries
        if entry.get("resource", {}).get("meta", {}).get("source")
    }

    pooled_entries: List[Dict[str, Any]] = []
    skipped_sources: List[str] = []
    for source in sorted(ky_sources):
        try:
            pooled_entries.extend(_fetch_insurance_plans_by_source(source, max_pages=max_pages))
        except HTTPException as exc:
            if exc.status_code == 502:
                skipped_sources.append(source)
                continue
            raise

    # De-duplicate by InsurancePlan id before applying requested filters.
    deduped_by_id: Dict[str, Dict[str, Any]] = {}
    for entry in pooled_entries:
        resource_id = entry.get("resource", {}).get("id")
        if resource_id:
            deduped_by_id[resource_id] = entry

    entries = [
        entry
        for entry in deduped_by_id.values()
        if _plan_matches_name_and_type(entry, validated_name_contains, validated_plan_type_text)
    ]
    query_mode = "ky_location_source_filter"

    sources = sorted(
        {
            entry.get("resource", {}).get("meta", {}).get("source", "?")
            for entry in entries
        }
    )
    return InsurancePlanFilterResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        filter_query=(
            f"GET /InsurancePlan?name:contains={validated_name_contains}"
            f"&plan-type:text={validated_plan_type_text}"
        ),
        query_mode=query_mode,
        total_insurance_plans=len(entries),
        sources=sources,
        skipped_sources=skipped_sources,
        entries=entries if include_entries else None,
    )
