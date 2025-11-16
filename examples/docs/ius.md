# Installation & User Specification

This generic operational document demonstrates how TraceFlow can include arbitrary Markdown artifacts that link back to requirements, tests, and risks.

## Installation checklist

1. Deploy the ingestion stack and confirm that the `traceflow` service exposes `/healthz`.
2. Verify that authentication (**REQ-001**) succeeds for a clinical account with the `clinician` role by following **TEST-001**.
3. Enable the identifier reconciliation engine (**REQ-005**) and record the resulting audit entry.
4. Capture Playwright evidence from **TEST-003** and attach it to the release ticket.

## Operational monitoring

- Operators review the reconciler dashboard hourly to ensure the guardrails for **RISK-001** and **RISK-002** remain in place.
- When a mismatch occurs, follow the escalation steps documented in the clinical SOP and reference the impacted **REQ-005** controls.
- Residual risks shall be re-evaluated whenever a new modality is onboarded.

## Traceability table

| Step                                    | Linked Requirement/Test | Linked Risk |
|-----------------------------------------|-------------------------|-------------|
| Configure audit webhooks                | REQ-005                 | RISK-001    |
| Re-run integration suite after upgrade  | TEST-003                | RISK-002    |
| Attach validation pack to submission    | TEST-001 / TEST-002     | RISK-003    |

## Sign-off

The implementation owner confirms that installation and operational verification were executed per procedure and that the TraceFlow validation pack is archived with the evidence references listed above.
