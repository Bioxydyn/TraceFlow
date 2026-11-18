# Installation & User Specification

Any Markdown file outside `requirements/`, `tests/` and `risks/` is included as it is, and the IDs
it mentions link to their items. The Tier 1 requirements cite the sections of this one as their
`IUS Source`.

## Installation checklist

1. Deploy the ingestion stack and confirm that the DemoScan service answers on `/healthz`.
2. Check that a clinician account can sign in (**REQ-001**) by following **TEST-001**.
3. Enable the identifier reconciliation engine (**REQ-005**) and check the audit entry it writes.
4. Run the intake journey (**TEST-005**) and attach the signed form to the release record.

## Operational monitoring

- Operators review the reconciliation queue hourly, so that **RISK-001** stays controlled.
- Pipeline warnings (**RISK-004**) are acknowledged by the reporting clinician.
- Re-evaluate the residual risks whenever a new modality is added.

## Traceability table

| Step                                   | Requirement / Test | Risk     |
|----------------------------------------|--------------------|----------|
| Configure audit webhooks               | REQ-005            | RISK-001 |
| Re-run the pipeline checks on upgrade  | TEST-003           | RISK-006 |
| Attach the validation pack to release  | TEST-001, TEST-005 | RISK-002 |
