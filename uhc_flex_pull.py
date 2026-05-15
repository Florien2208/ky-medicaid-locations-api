#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from uhc_flex_client import UhcFlexConfig, UhcFlexFHIRClient


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch(
    resource_type: str,
    *,
    payer_id: str = "hsid",
    use_public_sandbox: bool = False,
    state: Optional[str] = None,
    max_pages: Optional[int] = 5,
    count_per_page: int = 200,
) -> List[Dict[str, Any]]:
    if use_public_sandbox:
        cfg = UhcFlexConfig.public_sandbox()
    else:
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
    params: Dict[str, Any] = {}
    if state:
        if resource_type != "Location":
            raise ValueError("state filter is only supported for Location")
        params["address-state"] = state.upper()
    return client.iter_entries(resource_type, params=params, max_pages=max_pages, count_per_page=count_per_page)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull UHC Provider Directory via Optum FLEX FHIR")
    parser.add_argument("--payer-id", default="hsid", help="FLEX payer id (default hsid)")
    parser.add_argument("--public-sandbox", action="store_true", help="Use public stage sandbox base URL")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages per request (default 5)")
    args = parser.parse_args()

    out_dir = Path("outputs") / "uhc_flex" / (("sandbox" if args.public_sandbox else "production") + f"_{args.payer_id}")
    run_at = datetime.utcnow().isoformat() + "Z"

    # Indiana + Kentucky locations (bounded)
    for st in ["IN", "KY"]:
        entries = fetch(
            "Location",
            payer_id=args.payer_id,
            use_public_sandbox=args.public_sandbox,
            state=st,
            max_pages=args.max_pages,
        )
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

