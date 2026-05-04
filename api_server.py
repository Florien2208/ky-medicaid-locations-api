#!/usr/bin/env python3
"""
Shareable API for Centene Kentucky location fetch.
"""

import base64
import os
import re
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from anthem_client import AnthemConfig, AnthemFHIRClient
from humana_client import HumanaConfig, HumanaFHIRClient
from uhc_flex_client import UhcFlexConfig, UhcFlexFHIRClient

load_dotenv()

BASE_URL = "https://prod.api.centene.com/fhir/providerdirectory"


class FetchResponse(BaseModel):
    run_at: str = Field(description="ISO timestamp when fetch ran")
    state: str = Field(description="State filter applied to Location search")
    total_locations: int = Field(description="Count of locations returned for selected state")
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


def _fetch_locations_by_state(
    state: str, count_per_page: int = 200, max_pages: Optional[int] = None
) -> List[Dict[str, Any]]:
    headers = {"Authorization": _auth_header(), "Accept": "application/fhir+json"}
    entries: List[Dict[str, Any]] = []
    current_url: Optional[str] = f"{BASE_URL}/Location"
    current_params: Optional[Dict[str, Any]] = {"address-state": state, "_count": count_per_page}

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

class AnthemFetchResponse(BaseModel):
    run_at: str = Field(description="ISO timestamp when fetch ran")
    resource_type: str = Field(description="FHIR resource type fetched")
    total_entries: int = Field(description="Count of bundle entries returned")
    entries: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Raw FHIR bundle entries (only included when include_entries=true)",
    )


class AnthemResourceType(str, Enum):
    InsurancePlan = "InsurancePlan"
    Practitioner = "Practitioner"
    PractitionerRole = "PractitionerRole"
    Organization = "Organization"
    OrganizationAffiliation = "OrganizationAffiliation"
    Location = "Location"
    HealthcareService = "HealthcareService"


class HumanaResourceType(str, Enum):
    InsurancePlan = "InsurancePlan"
    Practitioner = "Practitioner"
    PractitionerRole = "PractitionerRole"
    Organization = "Organization"
    Location = "Location"


class UhcFlexResourceType(str, Enum):
    Organization = "Organization"
    OrganizationAffiliation = "OrganizationAffiliation"
    Practitioner = "Practitioner"
    PractitionerRole = "PractitionerRole"
    Network = "Network"
    Endpoint = "Endpoint"
    HealthcareService = "HealthcareService"
    InsurancePlan = "InsurancePlan"
    Location = "Location"


def _anthem_client() -> AnthemFHIRClient:
    try:
        cfg = AnthemConfig.from_env()
        return AnthemFHIRClient(cfg)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Anthem API not configured. Set ANTHEM_TOKEN_URL, ANTHEM_CLIENT_ID, "
                "ANTHEM_CLIENT_SECRET (and optionally ANTHEM_FHIR_BASE_URL). "
                f"Details: {exc}"
            ),
        ) from exc


@app.get(
    "/anthem/provider-directory/{resource_type}",
    response_model=AnthemFetchResponse,
    tags=["Anthem (Elevance Health)"],
    summary="Fetch Anthem Provider Directory resource entries",
)
def fetch_anthem_provider_directory(
    resource_type: AnthemResourceType,
    state: Optional[str] = Query(
        default=None,
        min_length=2,
        max_length=2,
        description="2-letter state code applied as address-state for Location searches.",
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
) -> AnthemFetchResponse:
    params: Dict[str, Any] = {}
    if state:
        if resource_type != AnthemResourceType.Location:
            raise HTTPException(status_code=422, detail="state filter is only supported for Location resource_type.")
        params["address-state"] = state.upper()

    client = _anthem_client()
    try:
        entries = client.iter_entries(resource_type.value, params=params, max_pages=max_pages)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        text = exc.response.text[:200] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Anthem API error {status}: {text}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Network error while calling Anthem API: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected error while calling Anthem API: {exc}") from exc

    return AnthemFetchResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        resource_type=resource_type.value,
        total_entries=len(entries),
        entries=entries if include_entries else None,
    )


@app.get(
    "/humana/provider-directory/{resource_type}",
    response_model=AnthemFetchResponse,
    tags=["Humana"],
    summary="Fetch Humana public Provider Directory resource entries (no auth)",
)
def fetch_humana_provider_directory(
    resource_type: HumanaResourceType,
    sandbox: bool = Query(
        default=False,
        description="When true, use Humana sandbox base URL instead of production.",
    ),
    state: Optional[str] = Query(
        default=None,
        min_length=2,
        max_length=2,
        description="2-letter state code applied as address-state for Location searches.",
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
) -> AnthemFetchResponse:
    params: Dict[str, Any] = {}
    if state:
        if resource_type != HumanaResourceType.Location:
            raise HTTPException(status_code=422, detail="state filter is only supported for Location resource_type.")
        params["address-state"] = state.upper()

    client = HumanaFHIRClient(HumanaConfig.sandbox() if sandbox else HumanaConfig.production())
    try:
        entries = client.iter_entries(resource_type.value, params=params, max_pages=max_pages)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        text = exc.response.text[:200] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Humana API error {status}: {text}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Network error while calling Humana API: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected error while calling Humana API: {exc}") from exc

    return AnthemFetchResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        resource_type=resource_type.value,
        total_entries=len(entries),
        entries=entries if include_entries else None,
    )


@app.get(
    "/uhc-flex/provider-directory/{resource_type}",
    response_model=AnthemFetchResponse,
    tags=["UnitedHealthcare (Optum FLEX)"],
    summary="Fetch UnitedHealthcare public Provider Directory resources (Optum FLEX)",
)
def fetch_uhc_flex_provider_directory(
    resource_type: UhcFlexResourceType,
    payer_id: str = Query(
        default="hsid",
        min_length=2,
        max_length=20,
        description="Optum FLEX payer id used in https://[payer].fhir.flex.optum.com/ (default hsid).",
    ),
    use_public_sandbox: bool = Query(
        default=False,
        description="When true, use public stage sandbox base URL (may not resolve in some networks).",
    ),
    state: Optional[str] = Query(
        default=None,
        min_length=2,
        max_length=2,
        description="2-letter state code applied as address-state for Location searches.",
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
) -> AnthemFetchResponse:
    params: Dict[str, Any] = {}
    if state:
        if resource_type != UhcFlexResourceType.Location:
            raise HTTPException(status_code=422, detail="state filter is only supported for Location resource_type.")
        params["address-state"] = state.upper()

    if use_public_sandbox:
        cfg = UhcFlexConfig.public_sandbox()
    else:
        # Prefer env-configured OAuth/bearer options, but allow payer_id override for base URL.
        env_cfg = UhcFlexConfig.from_env()
        cfg = UhcFlexConfig(
            base_url=f"https://{payer_id}.fhir.flex.optum.com/R4/",
            bearer_token=env_cfg.bearer_token,
            token_url=env_cfg.token_url,
            client_id=env_cfg.client_id,
            client_secret=env_cfg.client_secret,
            scope=env_cfg.scope,
        )
    client = UhcFlexFHIRClient(cfg)

    try:
        entries = client.iter_entries(resource_type.value, params=params, max_pages=max_pages)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        text = exc.response.text[:200] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"UHC FLEX API error {status}: {text}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Network error while calling UHC FLEX API: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected error while calling UHC FLEX API: {exc}") from exc

    return AnthemFetchResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        resource_type=resource_type.value,
        total_entries=len(entries),
        entries=entries if include_entries else None,
    )


@app.get(
    "/centene/ky-locations",
    response_model=FetchResponse,
    tags=["Centene"],
    summary="Fetch Kentucky locations",
)
def fetch_ky_locations(
    state: str = Query(
        default="KY",
        min_length=2,
        max_length=2,
        description="2-letter state code for Location search (default KY).",
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
) -> FetchResponse:
    selected_state = state.upper()
    entries = _fetch_locations_by_state(selected_state, max_pages=max_pages)
    sources = sorted(
        {
            entry.get("resource", {}).get("meta", {}).get("source", "?")
            for entry in entries
        }
    )
    return FetchResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        state=selected_state,
        total_locations=len(entries),
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
    ky_location_entries = _fetch_locations_by_state("KY", max_pages=max_pages)
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
