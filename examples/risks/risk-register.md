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
