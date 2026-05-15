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
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from anthem_client import AnthemConfig, AnthemFHIRClient
from humana_client import HumanaConfig, HumanaFHIRClient
from uhc_flex_client import UhcFlexConfig, UhcFlexFHIRClient
from caresource_client import CareSourceConfig, CareSourceFHIRClient

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
    title="Provider Directory API",
    description=(
        "Unified API for querying payer Provider Directory FHIR resources with "
        "optional state-based filtering."
    ),
    version="1.0.0",
    swagger_ui_parameters={
        # Keep Swagger UI stable for very large JSON responses by disabling
        # syntax highlighting (common source of call stack overflows).
        "syntaxHighlight": {"activated": False},
    },
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


def _dedupe_entries_by_resource_id(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    fallback: List[Dict[str, Any]] = []
    for entry in entries:
        resource = entry.get("resource", {})
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        if resource_type and resource_id:
            deduped[f"{resource_type}:{resource_id}"] = entry
        else:
            fallback.append(entry)
    return list(deduped.values()) + fallback


def _insurance_plan_matches_state(
    entry: Dict[str, Any],
    state_code: str,
    state_name: str,
    location_ids: Optional[set] = None,
) -> bool:
    resource = entry.get("resource", {})
    for area in resource.get("coverageArea", []):
        display = (area.get("display") or "").strip().upper()
        if display in {state_code, state_name.upper()}:
            return True

        reference = (area.get("reference") or "").strip()
        if location_ids:
            loc_id = _extract_location_id(reference)
            if loc_id and loc_id in location_ids:
                return True
    return False


def _extract_location_id(reference: str) -> Optional[str]:
    if not reference:
        return None

    ref = reference.strip()
    # Relative format: Location/{id}
    if ref.startswith("Location/"):
        return ref.split("/", 1)[1]

    # Absolute/compound formats:
    # .../Location/{id}, .../Location/{id}/_history/{ver}, urn:uuid:...
    match = re.search(r"/Location/([^/?#]+)", ref)
    if match:
        return match.group(1)

    return None


def _resource_references_location(resource: Any, location_ids: set) -> bool:
    if isinstance(resource, dict):
        ref = resource.get("reference")
        if isinstance(ref, str):
            loc_id = _extract_location_id(ref)
            if loc_id in location_ids:
                return True
        for value in resource.values():
            if _resource_references_location(value, location_ids):
                return True
        return False

    if isinstance(resource, list):
        for item in resource:
            if _resource_references_location(item, location_ids):
                return True
        return False

    return False


def _entry_matches_state_generic(
    entry: Dict[str, Any],
    state_code: str,
    state_name: str,
    location_ids: Optional[set] = None,
) -> bool:
    resource = entry.get("resource", {})
    target_values = {state_code.upper(), state_name.upper()}

    raw_address = resource.get("address", [])
    address_list = raw_address if isinstance(raw_address, list) else [raw_address]
    for address in address_list:
        if not isinstance(address, dict):
            continue
        address_state = (address.get("state") or "").strip().upper()
        if address_state in target_values:
            return True

    for area in resource.get("coverageArea", []):
        display = (area.get("display") or "").strip().upper()
        if display in target_values:
            return True

    if location_ids and _resource_references_location(resource, location_ids):
        return True

    return False


# @app.get("/", tags=["Health"])
def health_check() -> Dict[str, str]:
    return {"status": "ok"}

# @app.get("/routes", tags=["Health"])
def list_routes() -> Dict[str, List[str]]:
    return {
        "paths": sorted(
            {getattr(r, "path", "") for r in app.routes if getattr(r, "path", "")}
        )
    }

class AnthemFetchResponse(BaseModel):
    run_at: str = Field(description="ISO timestamp when fetch ran")
    resource_type: str = Field(description="FHIR resource type fetched")
    total_entries: int = Field(description="Count of bundle entries returned")
    entries: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Raw FHIR bundle entries (only included when include_entries=true)",
    )


class AnthemByIdResponse(BaseModel):
    run_at: str = Field(description="ISO timestamp when fetch ran")
    resource_type: str = Field(description="FHIR resource type fetched")
    resource_id: str = Field(description="FHIR resource id requested")
    entry: Dict[str, Any] = Field(description="Single FHIR entry/resource payload")


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

class CareSourceResourceType(str, Enum):
    InsurancePlan = "InsurancePlan"
    Practitioner = "Practitioner"
    PractitionerRole = "PractitionerRole"
    Organization = "Organization"
    OrganizationAffiliation = "OrganizationAffiliation"
    Location = "Location"
    HealthcareService = "HealthcareService"


def _anthem_state_query_candidates(resource_type: AnthemResourceType, state_code: str) -> List[Dict[str, str]]:
    if resource_type == AnthemResourceType.Location:
        return [{"address-state": state_code}]
    if resource_type == AnthemResourceType.InsurancePlan:
        return [
            {"coverage-area.address-state": state_code},
            {"coverage-area.state": state_code},
        ]
    if resource_type == AnthemResourceType.Practitioner:
        return [{"address-state": state_code}]
    if resource_type == AnthemResourceType.PractitionerRole:
        return [
            {"location.address-state": state_code},
            {"practitioner.address-state": state_code},
        ]
    if resource_type == AnthemResourceType.Organization:
        return [{"address-state": state_code}]
    if resource_type == AnthemResourceType.OrganizationAffiliation:
        return [
            {"location.address-state": state_code},
            {"participating-organization.address-state": state_code},
            {"organization.address-state": state_code},
        ]
    if resource_type == AnthemResourceType.HealthcareService:
        return [
            {"location.address-state": state_code},
            {"providedby.address-state": state_code},
        ]
    return []


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
    summary="Fetch Anthem Provider Directory FHIR resources",
)
def fetch_anthem_provider_directory(
    resource_type: AnthemResourceType,
    state: Optional[str] = Query(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "2-letter state code (e.g., KY, IN). For Location, applied as "
            "address-state. For other resources, state-aware filtering uses "
            "FHIR state/location relationships when available."
        ),
    ),
    include_entries: bool = Query(
        default=False,
        description="When true, include full raw entries in response.",
    ),
    max_pages: Optional[int] = Query(
        default=1,
        ge=1,
        description="Maximum number of paginated FHIR bundle pages to fetch (default 1).",
    ),
) -> AnthemFetchResponse:
    client = _anthem_client()
    try:
        if not state:
            entries = client.iter_entries(resource_type.value, params={}, max_pages=max_pages)
        else:
            s = state.upper()
            state_name_map = {
                "KY": "Kentucky",
                "IN": "Indiana",
                "TN": "Tennessee",
                "OH": "Ohio",
            }
            state_search_value = state_name_map.get(s, s)
            state_location_ids: Optional[set] = None

            entries: List[Dict[str, Any]] = []
            candidates = _anthem_state_query_candidates(resource_type, s)
            last_http_error: Optional[requests.HTTPError] = None

            # Resource-specific server-side filtering first (valid FHIR way).
            for candidate_params in candidates:
                try:
                    candidate_entries = client.iter_entries(
                        resource_type.value,
                        params=candidate_params,
                        max_pages=max_pages,
                    )
                    if resource_type == AnthemResourceType.InsurancePlan:
                        if state_location_ids is None:
                            state_locations = client.iter_entries(
                                "Location",
                                params={"address-state": s},
                                max_pages=max_pages,
                            )
                            state_location_ids = {
                                entry.get("resource", {}).get("id")
                                for entry in state_locations
                                if entry.get("resource", {}).get("id")
                            }
                        candidate_entries = [
                            entry
                            for entry in candidate_entries
                            if _insurance_plan_matches_state(entry, s, state_search_value, state_location_ids)
                        ]
                    if candidate_entries:
                        entries = candidate_entries
                        break
                except requests.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else 502
                    # Unsupported search params are common between payer implementations.
                    # Try the next candidate for this resource type.
                    if status in (400, 404, 422):
                        last_http_error = exc
                        continue
                    raise

            if not entries and resource_type == AnthemResourceType.InsurancePlan:
                # Some payer implementations return HTTP 200 + empty for chain params
                # even when state-relevant plans exist. Use a bounded fallback fetch
                # (single resource scan, no per-location fanout) to avoid zero false negatives.
                fallback_pages = max_pages if max_pages is not None else 1
                fallback_pages = max(1, min(fallback_pages, 3))
                unfiltered_entries = client.iter_entries(
                    resource_type.value,
                    params={},
                    max_pages=fallback_pages,
                )
                if state_location_ids is None:
                    state_locations = client.iter_entries(
                        "Location",
                        params={"address-state": s},
                        max_pages=max_pages,
                    )
                    state_location_ids = {
                        entry.get("resource", {}).get("id")
                        for entry in state_locations
                        if entry.get("resource", {}).get("id")
                    }
                entries = [
                    entry
                    for entry in unfiltered_entries
                    if _insurance_plan_matches_state(entry, s, state_search_value, state_location_ids)
                ]

            if not entries and resource_type != AnthemResourceType.InsurancePlan:
                # Fallback path: fetch resource once and apply state relationship filter locally.
                # This keeps behavior correct when a payer does not support a specific chain param.
                unfiltered_entries = client.iter_entries(resource_type.value, params={}, max_pages=max_pages)
                state_locations = client.iter_entries(
                    "Location",
                    params={"address-state": s},
                    max_pages=max_pages,
                )
                state_location_ids = {
                    entry.get("resource", {}).get("id")
                    for entry in state_locations
                    if entry.get("resource", {}).get("id")
                }

                if resource_type == AnthemResourceType.InsurancePlan:
                    entries = [
                        entry
                        for entry in unfiltered_entries
                        if _insurance_plan_matches_state(entry, s, state_search_value, state_location_ids)
                    ]
                else:
                    entries = [
                        entry
                        for entry in unfiltered_entries
                        if _entry_matches_state_generic(entry, s, state_search_value, state_location_ids)
                    ]

            # Preserve original API error behavior if no fallback path can help and we only saw hard failures.
            if not entries and last_http_error is not None:
                status = last_http_error.response.status_code if last_http_error.response is not None else 502
                if status >= 500:
                    raise last_http_error
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
    "/anthem/provider-directory/{resource_type}/{resource_id}",
    response_model=AnthemByIdResponse,
    tags=["Anthem (Elevance Health)"],
    summary="Fetch a single Anthem Provider Directory resource by id",
)
def fetch_anthem_provider_directory_by_id(
    resource_type: AnthemResourceType,
    resource_id: str = Path(
        ...,
        min_length=1,
        description="FHIR resource id (e.g., Location/abc -> use just abc).",
    ),
) -> AnthemByIdResponse:
    cleaned_id = resource_id.strip()
    if not cleaned_id:
        raise HTTPException(status_code=422, detail="resource_id cannot be empty.")
    if "/" in cleaned_id or " " in cleaned_id:
        raise HTTPException(
            status_code=422,
            detail="resource_id must be a raw id segment without slashes/spaces.",
        )

    client = _anthem_client()
    try:
        body = client._get_json(
            f"{client._cfg.base_url.rstrip('/')}/{resource_type.value}/{cleaned_id}",
            params=None,
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        if status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"{resource_type.value}/{cleaned_id} not found in Anthem directory.",
            ) from exc
        text = exc.response.text[:200] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Anthem API error {status}: {text}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Network error while calling Anthem API: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected error while calling Anthem API: {exc}") from exc

    entry: Dict[str, Any]
    if body.get("resourceType") == "Bundle":
        bundle_entries = body.get("entry", [])
        if not bundle_entries:
            raise HTTPException(
                status_code=404,
                detail=f"{resource_type.value}/{cleaned_id} not found in Anthem directory.",
            )
        entry = bundle_entries[0]
    else:
        entry = {"resource": body}

    return AnthemByIdResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        resource_type=resource_type.value,
        resource_id=cleaned_id,
        entry=entry,
    )


# @app.get(
#     "/humana/provider-directory/{resource_type}",
#     response_model=AnthemFetchResponse,
#     tags=["Humana"],
#     summary="Fetch Humana public Provider Directory resource entries (no auth)",
# )
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
        description="2-letter state code. For Location, mapped to address-state; for InsurancePlan, mapped to name:contains (full state name).",
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
        s = state.upper()
        if resource_type == HumanaResourceType.Location:
            params["address-state"] = s
        elif resource_type == HumanaResourceType.InsurancePlan:
            state_name_map = {
                "KY": "Kentucky",
                "IN": "Indiana",
                "TN": "Tennessee",
                "OH": "Ohio",
            }
            name_contains = state_name_map.get(s, s)
            params["name"] = name_contains

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


# @app.get(
#     "/uhc-flex/provider-directory/{resource_type}",
#     response_model=AnthemFetchResponse,
#     tags=["UnitedHealthcare (Optum FLEX)"],
#     summary="Fetch UnitedHealthcare public Provider Directory resources (Optum FLEX)",
# )
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
        text = exc.response.text[:400] if exc.response is not None else str(exc)
        hint = ""
        if status in (401, 403):
            hint = (
                " Hint: FLEX directory endpoints typically require OAuth2 client_credentials (or a bearer token) "
                "with public/*.read scopes. Configure UHC_FLEX_BEARER_TOKEN, or UHC_FLEX_TOKEN_URL + "
                "UHC_FLEX_CLIENT_ID + UHC_FLEX_CLIENT_SECRET (+ UHC_FLEX_SCOPE)."
            )
        raise HTTPException(status_code=status, detail=f"UHC FLEX API error {status}: {text}{hint}") from exc
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

# @app.get(
#     "/caresource/provider-directory/{resource_type}",
#     response_model=AnthemFetchResponse,
#     tags=["CareSource"],
#     summary="Fetch CareSource Provider Directory resources (config required)",
# )
def fetch_caresource_provider_directory(
    resource_type: CareSourceResourceType,
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
        if resource_type != CareSourceResourceType.Location:
            raise HTTPException(status_code=422, detail="state filter is only supported for Location resource_type.")
        params["address-state"] = state.upper()

    try:
        client = CareSourceFHIRClient(CareSourceConfig.from_env())
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "CareSource API not configured. Set CARESOURCE_FHIR_BASE_URL and either CARESOURCE_BEARER_TOKEN "
                "or CARESOURCE_TOKEN_URL + CARESOURCE_CLIENT_ID + CARESOURCE_CLIENT_SECRET (+ CARESOURCE_SCOPE). "
                f"Details: {exc}"
            ),
        ) from exc

    try:
        entries = client.iter_entries(resource_type.value, params=params, max_pages=max_pages)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        text = exc.response.text[:400] if exc.response is not None else str(exc)
        raise HTTPException(status_code=status, detail=f"CareSource API error {status}: {text}") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Network error while calling CareSource API: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected error while calling CareSource API: {exc}") from exc

    return AnthemFetchResponse(
        run_at=datetime.utcnow().isoformat() + "Z",
        resource_type=resource_type.value,
        total_entries=len(entries),
        entries=entries if include_entries else None,
    )


# @app.get(
#     "/centene/ky-locations",
#     response_model=FetchResponse,
#     tags=["Centene"],
#     summary="Fetch Kentucky locations",
# )
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


# @app.get(
#     "/centene/insurance-plans/kentucky-medicaid",
#     response_model=InsurancePlanFilterResponse,
#     tags=["Centene"],
#     summary="Fetch InsurancePlan by Kentucky + Medicaid filter",
# )
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
