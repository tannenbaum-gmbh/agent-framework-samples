# Internal Cloud Architecture Standard: Reliability and Recovery

> Demonstration content for Contoso. These requirements are fictional and must
> not be treated as Microsoft guidance or production policy.

**Owner:** Enterprise Architecture Review Board  
**Standard version:** 2026.10

## Workload tiers

| Tier | Business impact | Availability target | RTO | RPO |
| --- | --- | --- | --- | --- |
| Tier 0 | Safety, identity, or enterprise-wide critical | 99.99% | 15 minutes | 5 minutes |
| Tier 1 | Revenue or customer-facing critical | 99.95% | 1 hour | 15 minutes |
| Tier 2 | Important internal workload | 99.9% | 8 hours | 4 hours |
| Tier 3 | Development or noncritical | Best effort | 2 business days | 24 hours |

## Mandatory requirements

### REL-001 — Failure-domain design

- Production tier 0 and tier 1 compute MUST span availability zones when the
  selected Azure region and service support zones.
- A zonal dependency MUST be identified as a risk and approved by the Enterprise
  Architecture Review Board.
- Health probes MUST validate dependency readiness, not only process liveness.

### REL-002 — Regional recovery

- Tier 0 workloads MUST use active-active deployment across two Azure regions.
- Tier 1 workloads MUST maintain a tested warm standby in a paired or approved
  secondary region.
- Tier 2 workloads MAY use backup-and-restore recovery if the stated RTO and RPO
  are demonstrated.

### REL-003 — Data protection

- Production data MUST use automated backups with soft delete where supported.
- Backup retention MUST be 35 days operationally and 12 monthly recovery points.
- Restore tests MUST run quarterly for tier 0 and tier 1 and semiannually for
  tier 2. A successful backup without a restore test is not recovery evidence.

### REL-004 — Validation

- Tier 0 and tier 1 services MUST run a failure-mode exercise before production
  approval and at least twice per year afterward.
- Load tests MUST demonstrate 150% of forecast peak demand without violating
  service-level objectives.
- Architecture records MUST quantify the aggregate availability of critical
  synchronous dependencies.

## Required evidence

The production readiness review must include a dependency map, RTO/RPO test
results, backup restore evidence, regional failover runbook, and named service
owner.
