# Internal Cloud Architecture Standard: Governance and Cost

> Demonstration content for Contoso. These requirements are fictional and must
> not be treated as Microsoft guidance or production policy.

**Owner:** Cloud Center of Excellence  
**Standard version:** 2026.10

## Mandatory requirements

### GOV-001 — Deployment and configuration

- Production resources MUST be deployed through version-controlled Bicep or
  Terraform. Manual portal changes are emergency-only and MUST be reconciled
  into code within one business day.
- Deployments MUST run policy and security checks in CI before promotion.
- Production and nonproduction MUST use separate subscriptions.

### GOV-002 — Resource metadata

Every resource MUST include these tags:

| Tag | Required value |
| --- | --- |
| `applicationId` | Registered application identifier |
| `owner` | Accountable team distribution list |
| `environment` | `prod`, `test`, `dev`, or `sandbox` |
| `dataClassification` | `public`, `internal`, `confidential`, or `restricted` |
| `costCenter` | Finance-issued cost center |
| `criticality` | `tier0`, `tier1`, `tier2`, or `tier3` |

### COST-001 — Cost controls

- Each production workload MUST have a monthly budget with alerts at 50%, 80%,
  and 100% of forecast spend.
- Nonproduction compute MUST use an automated shutdown or scale-to-zero schedule
  unless a documented test requires continuous operation.
- Architecture proposals with forecast cloud spend above USD 25,000 per month
  MUST include a three-year cost model and FinOps approval.
- Resources with average utilization below 20% for 30 days MUST be reviewed for
  rightsizing.

### GOV-003 — Architecture approval

Enterprise Architecture Review Board approval is required before production when
any of these conditions apply:

- workload criticality is tier 0 or tier 1;
- restricted data is stored or processed;
- public ingress bypasses the approved edge service;
- a new Azure service not present in the approved service catalog is introduced;
- forecast spend exceeds USD 25,000 per month; or
- an exception to any `SEC-*`, `REL-*`, `GOV-*`, or `COST-*` requirement is
  requested.

## Decision records

Material decisions MUST be recorded in an architecture decision record that
states the context, options considered, decision, consequences, owner, and review
date.
