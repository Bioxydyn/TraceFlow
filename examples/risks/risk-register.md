# Risk Register

TraceFlow libraries track software hazards alongside the requirements and tests that mitigate them.

## RISK-001: Incorrect study-patient association

Hazardous Situation: Clinician views the wrong patient's images believing they are correct.

Harm: Misdiagnosis or inappropriate treatment.

Cause: Race condition during HL7/DICOM message processing leading to incorrect `PatientID` or `AccessionNumber`.

Severity: High

Probability: Medium

Controls: Identifier reconciliation service (**REQ-005**) exercised by **TEST-002**.

Residual Severity: Medium

Residual Probability: Low

Residual Risk: Operator review plus automatic quarantine when mismatches occur.

## RISK-002: Undetected imaging artifacts

Hazardous Situation: Poor quality MRI acquisitions flow through the analysis pipeline without alerts.

Harm: False clinical conclusions or delayed treatment.

Cause: Lack of automated QA gates between the import module (**REQ-002**) and analysis pipeline (**REQ-003**).

Severity: Medium

Probability: Medium

Controls: Automated test matrix (**TEST-003**) and image QC hooks inside the pipeline.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Clinician-facing dashboard highlights residual warnings for manual acknowledgement.

## RISK-003: Audit trail corruption

Hazardous Situation: Investigators cannot reconstruct prior runs because audit entries were dropped.

Harm: Compliance breach or inability to support safety investigations.

Cause: Misconfigured storage or missing webhook callbacks when persisting audit artifacts from **REQ-005**.

Severity: Medium

Probability: Low

Controls: Continuous integration checks (**TEST-003**) and deployment checklist in `docs/ius.md`.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable provided long-term storage health checks pass weekly.

## RISK-004: Unauthorized workspace access

Hazardous Situation: An unapproved clinician launches the UI and edits live studies without authentication.

Harm: Breach of patient confidentiality and risk of tampering with diagnostic evidence.

Cause: Misconfigured identity provider or bypass of the login screen covered in **REQ-001**.

Severity: High

Probability: Low

Controls: OpenID Connect gateway, MFA, and **TEST-001** regression runs.

Residual Severity: Medium

Residual Probability: Low

Residual Risk: Acceptable when the security team performs quarterly access recertification.

## RISK-005: Notification backlog for identifier mismatches

Hazardous Situation: Identifier reconciliation alerts pile up, so clinicians miss urgent mismatches.

Harm: Delayed detection of study/patient mix-ups (see **REQ-005**).

Cause: Downstream webhook queue saturation or operator console outages.

Severity: Medium

Probability: Medium

Controls: Autoscaling webhook workers plus the workflow exercised by **TEST-002**.

Residual Severity: Low

Residual Probability: Medium

Residual Risk: Escalate to the on-call operator when more than five alerts remain untriaged for over 30 minutes.

## RISK-006: Exported report mismatch

Hazardous Situation: Generated PDF or DICOM SR exports omit the source dataset IDs.

Harm: Inability to justify clinical findings or to reconstruct the original evidence trail.

Cause: Faulty serialization of the output schema defined in **REQ-004**.

Severity: Medium

Probability: Low

Controls: Automated CI pipeline (**TEST-003**) that validates exports against golden files.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable when QA signs off release bundles that include checksum manifests.

## RISK-007: Pipeline drift between releases

Hazardous Situation: Model weights or preprocessing steps change without validation, yielding inconsistent diagnoses.

Harm: False positives/negatives that could trigger incorrect therapy.

Cause: Manual edits to **REQ-003** pipeline components without rerunning validation.

Severity: High

Probability: Medium

Controls: Continuous integration gates in **TEST-003** plus code reviews for every pipeline update.

Residual Severity: Medium

Residual Probability: Low

Residual Risk: Acceptable once deployment reports attach the validation pack and a signed change record.

## RISK-008: Data retention policy breach

Hazardous Situation: Backups omit audit logs, preventing reconstruction of historic identifier fixes.

Harm: Compliance violations with ICH-GCP and inability to support investigations.

Cause: Retention scripts ignore the storage bucket defined in **REQ-005**.

Severity: Medium

Probability: Medium

Controls: Scheduled integrity checks plus **TEST-003** to validate log export scripts.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable when quarterly SOP reviews confirm retention evidence is archived.

## RISK-009: Automated test evidence missing

Hazardous Situation: Regression runs referenced by **TEST-003** fail but their output is not included in the validation pack.

Harm: Release approvals rely on incomplete evidence trails.

Cause: Operator forgets to pass `--playwright-dir` or the CI job skips the autotest block.

Severity: Medium

Probability: Medium

Controls: TraceFlow CLI guards that raise on missing artifacts plus the process captured in `docs/ius.md`.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable provided release managers verify example.pdf before sign-off.

## RISK-010: External interface misconfiguration

Hazardous Situation: Downstream systems consume reconciliation webhooks with the wrong schema and silently drop events.

Harm: Identifier mismatches never reach clinical users even though TraceFlow generated the alerts.

Cause: Integration guides diverge from the specification in **REQ-005** or design doc steps.

Severity: Medium

Probability: Medium

Controls: Installation & User Specification walkthrough (`docs/ius.md`) and contract tests executed as part of **TEST-002**.

Residual Severity: Low

Residual Probability: Medium

Residual Risk: Acceptable once each release captures signed integration test outputs in the validation pack.

## RISK-011: Manual test paperwork incomplete

Hazardous Situation: Manual acceptance tests (**TEST-001**) are executed but signatures or pass/fail states are unsigned, invalidating the safety case.

Harm: Regulatory submissions may be rejected, delaying patient access.

Cause: Clinicians forget to fill the interactive PDF fields exported by TraceFlow.

Severity: Low

Probability: Medium

Controls: The test cover sheet plus electronic signature workflow baked into TraceFlow forms.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable when QA audits confirm PDF form completion before release.
