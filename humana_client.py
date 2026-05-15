import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class HumanaConfig:
    base_url: str

    @staticmethod
    def production() -> "HumanaConfig":
        return HumanaConfig(base_url="https://fhir.humana.com/api/")

    @staticmethod
    def sandbox() -> "HumanaConfig":
        return HumanaConfig(base_url="https://sandbox-fhir.humana.com/api/")


class HumanaFHIRClient:
    def __init__(self, cfg: HumanaConfig):
        self._cfg = cfg

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/fhir+json"}

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        retries = 3
        backoff = 1.0
        last: Optional[requests.Response] = None

        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, headers=self._headers(), params=params, timeout=60)
                last = resp
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 502, 503, 504) and attempt < retries:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                        continue
                    except ValueError:
                        pass
                time.sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()

        assert last is not None
        last.raise_for_status()
        raise RuntimeError("Unreachable")

    def iter_entries(
        self,
        resource_type: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        count_per_page: int = 200,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []

        base = self._cfg.base_url.rstrip("/") + "/"
        url: Optional[str] = f"{base}{resource_type}"
        cur_params: Optional[Dict[str, Any]] = dict(params or {})
        cur_params["_count"] = count_per_page

        page = 1
        while url:
            body = self._get_json(url, params=cur_params)
            entries.extend(body.get("entry", []))

            if max_pages is not None and page >= max_pages:
                break

            next_url = None
            for link in body.get("link", []):
                if link.get("relation") == "next":
                    next_url = link.get("url")
                    break

            url = next_url
            cur_params = None
            page += 1

        return entries

