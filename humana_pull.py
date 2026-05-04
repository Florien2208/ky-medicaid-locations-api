#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from humana_client import HumanaConfig, HumanaFHIRClient


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch(
    resource_type: str,
    *,
    sandbox: bool = False,
    state: Optional[str] = None,
    max_pages: Optional[int] = 5,
    count_per_page: int = 200,
) -> List[Dict[str, Any]]:
    cfg = HumanaConfig.sandbox() if sandbox else HumanaConfig.production()
    client = HumanaFHIRClient(cfg)
    params: Dict[str, Any] = {}
    if state:
        if resource_type != "Location":
            raise ValueError("state filter is only supported for Location")
        params["address-state"] = state.upper()
    return client.iter_entries(resource_type, params=params, max_pages=max_pages, count_per_page=count_per_page)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Humana public Provider Directory FHIR resources")
    parser.add_argument("--sandbox", action="store_true", help="Use sandbox base URL")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages per request (default 5)")
    args = parser.parse_args()

    out_dir = Path("outputs") / "humana" / ("sandbox" if args.sandbox else "production")
    run_at = datetime.utcnow().isoformat() + "Z"

    # Indiana + Kentucky Locations (bounded by max-pages default to avoid huge pulls)
    for st in ["IN", "KY"]:
        entries = fetch("Location", sandbox=args.sandbox, state=st, max_pages=args.max_pages)
        _write_json(
            out_dir / f"{st.lower()}_locations.json",
            {
                "run_at": run_at,
                "resourceType": "Location",
                "state": st,
                "total_entries": len(entries),
                "entries": entries,
            },
        )


if __name__ == "__main__":
    main()

