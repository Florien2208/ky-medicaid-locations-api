# Provider Directory API Implementation Plan

## Research Summary

Based on the analysis of the provided APIs, here's the current status and implementation plan for pulling Medicaid providers from Kentucky and Tennessee.

## Kentucky Medicaid Providers

### 1. **Centene Partners API** (HIGHEST PRIORITY)
- **Status**: Requires registration
- **URL**: https://partners.centene.com/apiDetail/8122bc9c-43d6-4a2a-b6be-2272df8b8566
- **Action Required**: Register for free account to download API documentation
- **Expected Query**: Similar to `GET /providers?state=KY&plan_type=Medicaid`
- **Timeline**: 1-2 days for registration and setup

### 2. **Humana Provider Directory API**
- **Status**: Documentation accessible, need actual endpoint
- **URL**: https://developers.humana.com/provider-directory-api/doc
- **Action Required**: Navigate documentation to find FHIR endpoint
- **Expected Query**: `GET /InsurancePlan?name:contains=Kentucky&plan-type:text=Medicaid`
- **Timeline**: 1 day to find endpoint and test

### 3. **Mass.gov Provider Directory API**
- **Status**: Reference implementation only
- **Note**: This appears to be Massachusetts-specific, not Kentucky
- **Action Required**: Find Kentucky-specific equivalent or skip

## Tennessee TennCare Providers

### 1. **BlueCross BlueShield Tennessee (BlueCare)** (HIGHEST PRIORITY)
- **Status**: Best documented according to research
- **Type**: FHIR R4 + DaVinci PDEX Plan-Net
- **Action Required**: Access developer portal for actual endpoint
- **Resources**: Practitioner, PractitionerRole, Organization, Location, HealthcareService, InsurancePlan
- **Timeline**: 2-3 days for portal access and implementation

### 2. **Wellpoint (Amerigroup Tennessee)**
- **Status**: Endpoints provided but connection issues
- **Provided URLs**: 
  - `https://api.providerdir.anthem.com/public/tn/v1/providers`
  - FHIR: `https://api.providerdir.anthem.com/fhir/PlanNet/*`
- **Issue**: DNS resolution failing - URLs may be outdated
- **Action Required**: Find current Elevance/Wellpoint API documentation
- **Timeline**: 2-3 days to find correct endpoints

### 3. **UnitedHealthcare Community Plan Tennessee**
- **Status**: Mentioned but no specific endpoints provided
- **Action Required**: Contact UHC or find their Tennessee-specific API documentation
- **Timeline**: 3-5 days depending on response time

## Implementation Priority Order

1. **Centene Kentucky** - Register and implement (2-3 days)
2. **BlueCare Tennessee** - Access developer portal (2-3 days)  
3. **Humana Kentucky** - Find endpoint in documentation (1-2 days)
4. **Wellpoint Tennessee** - Find correct endpoints (2-3 days)
5. **UHC Tennessee** - Contact for documentation (3-5 days)

## Technical Implementation

### FHIR Query Pattern
For FHIR-compliant APIs, use this pattern:
```
GET /InsurancePlan?name:contains=Kentucky&plan-type:text=Medicaid
GET /InsurancePlan?name:contains=Tennessee&plan-type:text=Medicaid
```

### REST API Pattern
For traditional REST APIs:
```
GET /providers?state=KY&plan_type=Medicaid
GET /providers?state=TN&plan_type=Medicaid
```

### Authentication
- Most APIs will require API keys or OAuth tokens
- Register for developer accounts where required
- Store credentials securely (environment variables)

## Expected Data Structure

Based on FHIR R4 standards, expect these resource types:
- **Practitioner**: Individual healthcare providers
- **Organization**: Healthcare facilities/groups
- **Location**: Physical addresses and service locations
- **PractitionerRole**: Provider specialties and affiliations
- **InsurancePlan**: Medicaid plan details

## Next Immediate Actions

1. **TODAY**: Register for Centene Partners API
2. **TODAY**: Navigate Humana documentation for actual endpoint
3. **TOMORROW**: Research BlueCare developer portal access
4. **THIS WEEK**: Contact Wellpoint/Elevance for current API documentation
5. **THIS WEEK**: Reach out to UHC Tennessee for API access

## Success Metrics

- Successfully pull provider lists from at least 2 Kentucky sources
- Successfully pull provider lists from at least 2 Tennessee sources
- Implement standardized data format for all sources
- Create automated refresh mechanism for provider data

## Risk Mitigation

- Some APIs may require business partnerships rather than just developer access
- Rate limiting may restrict bulk data pulls
- Data may be paginated requiring multiple API calls
- Some providers may not expose all Medicaid networks publicly