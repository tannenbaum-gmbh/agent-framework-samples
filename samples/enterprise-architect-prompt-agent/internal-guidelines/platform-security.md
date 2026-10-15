# Internal Cloud Architecture Standard: Platform Security

> Demonstration content for Contoso. These requirements are fictional and must
> not be treated as Microsoft guidance or production policy.

**Owner:** Cloud Security Architecture  
**Standard version:** 2026.10  
**Review cycle:** Annual

## Scope

These controls apply to every Azure workload. A stricter regulatory standard
takes precedence when both apply.

## Mandatory requirements

### SEC-001 — Identity-first access

- Workload-to-workload authentication MUST use managed identities when the Azure
  service supports them.
- New application secrets MUST NOT be stored in source code, deployment
  parameters, or container environment variables.
- Human administrative access MUST use Microsoft Entra ID groups, phishing-
  resistant MFA, and just-in-time privileged access.

### SEC-002 — Network exposure

- Tier 0 and tier 1 data services MUST disable public network access and use
  private endpoints.
- Internet ingress MUST terminate at the approved edge service with Web
  Application Firewall protection enabled.
- East-west traffic MUST be explicitly allowed; broad `Any/Any` network rules
  are prohibited.

### SEC-003 — Encryption and keys

- All endpoints MUST require TLS 1.2 or newer.
- Platform-managed encryption keys are the default. Customer-managed keys are
  required only for workloads whose data classification or contract explicitly
  mandates them.
- Keys and certificates MUST be held in Azure Key Vault and rotated at least
  every 180 days.

### SEC-004 — Security evidence

- Diagnostic logs MUST be sent to the central Log Analytics workspace.
- Tier 0 and tier 1 resources MUST enable Microsoft Defender for Cloud plans
  applicable to their resource types.
- Security logs MUST be retained online for 90 days and archived for 365 days.

## Exceptions

An exception requires a documented threat assessment, compensating controls,
expiry date no later than 180 days, and approval from the Cloud Security
Architect.
