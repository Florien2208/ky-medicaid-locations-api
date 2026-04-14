# Provider Directory API Research

## Kentucky Health Plans

### 1. Mass.gov Provider Directory API
- URL: https://www.mass.gov/info-details/provider-directory-api
- Type: FHIR R4 based
- Target Query: `GET /InsurancePlan?name:contains=Kentucky&plan-type:text=Medicaid`

### 2. Centene Partners API
- URL: https://partners.centene.com/apiDetail/8122bc9c-43d6-4a2a-b6be-2272df8b8566
- Requires: Free account registration
- Focus: Kentucky Medicaid providers

### 3. Humana Provider Directory API
- URL: https://developers.humana.com/provider-directory-api/doc
- Focus: Kentucky Medicaid providers
- Documentation: Limited availability

## Tennessee TennCare APIs

### 1. Wellpoint (Amerigroup Tennessee)
- Base URL: `https://api.providerdir.anthem.com/public/tn/v1/providers`
- FHIR Endpoints:
  - `https://api.providerdir.anthem.com/fhir/PlanNet/Practitioner`
  - `https://api.providerdir.anthem.com/fhir/PlanNet/Organization`
  - `https://api.providerdir.anthem.com/fhir/PlanNet/Location`

### 2. BlueCross BlueShield Tennessee (BlueCare)
- Developer Portal: Available
- Type: FHIR R4 + DaVinci PDEX Plan-Net
- Resources: Practitioner, PractitionerRole, Organization, Location, HealthcareService, InsurancePlan, Endpoint

### 3. UnitedHealthcare Community Plan Tennessee
- Type: CMS interoperability APIs (FHIR R4)
- Resources: Practitioner, Organization, Location, PractitionerRole
- Geographic coverage: East/Middle/West Tennessee regions

## Implementation Priority
1. BlueCare Tennessee (best documentation)
2. Wellpoint Tennessee (established endpoints)
3. Centene Kentucky (requires registration)
4. Mass.gov API (reference implementation)
5. Humana (limited docs)
6. UHC Tennessee (regional approach)