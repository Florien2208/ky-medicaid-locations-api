#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthem_client import AnthemConfig, AnthemFHIRClient


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch(
    resource_type: str,
    *,
    state: Optional[str] = None,
    max_pages: Optional[int] = None,
    count_per_page: int = 200,
) -> List[Dict[str, Any]]:
    client = AnthemFHIRClient(AnthemConfig.from_env())
    params: Dict[str, Any] = {}
    if state:
        if resource_type != "Location":
            raise ValueError("state filter is only supported for Location")
        params["address-state"] = state.upper()
    return client.iter_entries(resource_type, params=params, max_pages=max_pages, count_per_page=count_per_page)


def main() -> None:
    out_dir = Path("outputs") / "anthem"
    run_at = datetime.utcnow().isoformat() + "Z"

    # Pull Indiana Locations by address-state=IN (top priority)
    locations_in = fetch("Location", state="IN", max_pages=None)
    _write_json(
        out_dir / "indiana_locations.json",
        {
            "run_at": run_at,
            "resourceType": "Location",
            "state": "IN",
            "total_entries": len(locations_in),
            "entries": locations_in,
        },
    )

    # Optionally pull Kentucky Locations as well (no harm even if empty)
    locations_ky = fetch("Location", state="KY", max_pages=None)
    _write_json(
        out_dir / "kentucky_locations.json",
        {
            "run_at": run_at,
            "resourceType": "Location",
            "state": "KY",
            "total_entries": len(locations_ky),
            "entries": locations_ky,
        },
    )


if __name__ == "__main__":
    main()

