# Risk Register

Each risk names the hazardous situation, its harm and cause, and is scored for severity and
probability before and after its controls. A control that a requirement implements names that
requirement, and `Test evidence:` names the tests that show the control works.

## RISK-001: Incorrect study-patient association

Hazardous Situation: A clinician views the wrong patient's images believing they are correct.

Harm: Misdiagnosis or inappropriate treatment.

Cause: A race condition while processing HL7 and DICOM messages assigns the wrong `PatientID` or
`AccessionNumber`.

Severity: High

Probability: Medium

Controls: Identifier reconciliation quarantines any study whose identifiers disagree (REQ-005).

Test evidence: TEST-002, TEST-005.

Residual Severity: Medium

Residual Probability: Low

Residual Risk: Acceptable: an operator reviews every quarantined study before it is released.

## RISK-002: Unauthorised access to studies

Hazardous Situation: Someone without an account opens studies in the user interface.

Harm: Breach of patient confidentiality.

Cause: A misconfigured identity provider, or a way round the login screen.

Severity: High

Probability: Low

Controls: Every page and API call needs a signed-in user (REQ-001).

Test evidence: TEST-001.

Residual Severity: Medium

Residual Probability: Low

Residual Risk: Acceptable with the quarterly access review in the operating procedure.

## RISK-003: Corrupt or partial study import

Hazardous Situation: A truncated or non-DICOM file is imported and analysed as if it were complete.

Harm: Analysis results that do not reflect the acquired images.

Cause: The import accepts files without validating them.

Severity: Medium

Probability: Medium

Controls: The import refuses files that are not valid DICOM and records each series' slice count
(REQ-002).

Test evidence: TEST-004.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable.

## RISK-004: Undetected imaging artefacts

Hazardous Situation: Poor-quality acquisitions flow through the analysis pipeline without a
warning.

Harm: Wrong conclusions drawn from the analysis, or delayed treatment.

Cause: No quality gates between the pipeline's stages.

Severity: Medium

Probability: Medium

Controls: Pipeline quality checks at each stage (REQ-003).

Test evidence: TEST-003.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable: residual warnings are shown to the clinician for acknowledgement.

## RISK-005: Exported results without their source

Hazardous Situation: Exported results omit the IDs of the datasets they came from.

Harm: Findings cannot be traced back to the images that support them.

Cause: A fault in the export format.

Severity: Medium

Probability: Low

Controls: Exports carry each source series' identifiers, checked against golden files (REQ-004).

Test evidence: TEST-003.

Residual Severity: Low

Residual Probability: Low

Residual Risk: Acceptable.

## RISK-006: Pipeline changes without revalidation

Hazardous Situation: A pipeline step changes between releases without being validated again.

Harm: Inconsistent results between releases.

Cause: Pipeline code is edited without rerunning the validation checks.

Severity: High

Probability: Medium

Controls: The release pipeline checks run on every change to the pipeline (REQ-003), and a release
needs this validation pack signed.

Test evidence: TEST-003.

Residual Severity: Medium

Residual Probability: Low

Residual Risk: Acceptable with code review of every pipeline change.
