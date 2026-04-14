# Health Research - Provider Directory APIs

This folder contains all research and implementation code for pulling Medicaid providers from Kentucky and Tennessee health plan APIs.

## Project Overview

**Objective**: Pull Medicaid providers using the query pattern:
```
GET /InsurancePlan?name:contains=Kentucky&plan-type:text=Medicaid
```

**Target States**: Kentucky and Tennessee
**Target Plan Type**: Medicaid

## File Structure

### Documentation
- `provider_directory_research.md` - Complete API research and findings
- `implementation_plan.md` - Detailed action plan and next steps
- `api_test_results.json` - Results from API accessibility testing

### Implementation Code
- `medicaid_provider_puller_simple.py` - Main testing script (working version)
- `medicaid_provider_puller.py` - Original comprehensive version
- `provider_directory_client.py` - Generic FHIR client implementation
- `health_plan_apis.py` - Specific API implementations for each health plan
- `api_implementation_template.py` - Template for future implementation

### Configuration
- `requirements.txt` - Python dependencies
- `README.md` - This file

## Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the API testing script:
```bash
python medicaid_provider_puller_simple.py
```

## Shareable API with Swagger

This project now includes a single API endpoint that wraps the Kentucky Location pull:

- `GET /centene/ky-locations`
- Swagger UI: `http://localhost:8000/docs`

### Run locally

1. Make sure `.env` has:
```bash
CENTENE_USER=your_username
CENTENE_PASS=your_password
```
2. Start API server:
```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```
3. Open Swagger docs:
```bash
http://localhost:8000/docs
```

### Endpoint behavior

- Default response returns:
  - `run_at`
  - `total_ky_locations`
  - `sources`
- Add `?include_entries=true` to include raw FHIR entries.

### Deploy (easy option: Render)

1. Push this folder to a GitHub repo.
2. Go to [Render](https://render.com/) and create a new **Web Service**.
3. Use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn api_server:app --host 0.0.0.0 --port $PORT`
4. Add environment variables in Render:
   - `CENTENE_USER`
   - `CENTENE_PASS`
5. Deploy, then share:
   - `https://<your-service>.onrender.com/docs`

## API Status Summary

### Kentucky Medicaid Providers
- **Centene Partners API**: Requires registration ⚠️
- **Humana Provider Directory**: Documentation accessible, need endpoint ⚠️
- **Mass.gov API**: Reference only (Massachusetts-specific) ❌

### Tennessee TennCare Providers
- **BlueCare Tennessee**: Best documented, need developer portal access ⚠️
- **Wellpoint/Anthem**: Endpoints inaccessible (DNS issues) ❌
- **UnitedHealthcare**: Need documentation ⚠️

## Next Steps

1. Register for Centene Partners API
2. Access BlueCare developer portal
3. Find Humana's actual FHIR endpoint
4. Contact Wellpoint/Elevance for current endpoints
5. Reach out to UHC Tennessee for API access

## Usage Notes

- All scripts are designed to work with FHIR R4 compliant APIs
- Authentication will be required for most production APIs
- Rate limiting and pagination should be expected
- Store API credentials securely using environment variables

## Contact Information

For questions about this research or implementation, refer to the detailed documentation in the markdown files.