# Platform Tests

Each test names the requirements it verifies on `**Requirement ID:**` lines. A `manualtest` block
prints a fillable form (Pass / Fail / Skip and comments) for a tester; an `autotest` block prints
the evidence record of that ID from the test-results folder.

```testcoverpage
```

## TEST-001: User authentication

**Requirement ID:** REQ-001

### Test Steps:

1. Navigate to the login page.
2. Enter a valid email address and password.
3. Click the "Log in" button.

### Expected Result:

The user is logged in and redirected to the dashboard, which shows their role.

### Test Outcome:

```manualtest
```

### Automated evidence:

```autotest
E2E-LOGIN-001
```

## TEST-002: Identifier reconciliation

**Requirement ID:** REQ-005

### Test Steps:

1. Send a study whose DICOM PatientID differs from the HL7 PatientID.
2. Check the study is quarantined and not stored.
3. Check the mismatch appears in the operator review queue.

### Expected Result:

The mismatched study is quarantined and visible for operator review; a matching study is stored.

### Automated evidence:

```autotest
UNIT-RECONCILE-001
```

## TEST-003: Pipeline validation

**Requirement ID:** REQ-003
**Requirement ID:** REQ-004

### Test Steps:

1. Run the release pipeline checks for preprocessing, segmentation and classification.
2. Review the exported results against the golden files.
3. Record the outcome below.

### Expected Result:

The pipeline checks pass and the exports match the golden files.

### Automated evidence:

```autotest
CI-PIPELINE-CHECKS
```

### Test Outcome:

```manualtest
```

## TEST-004: MRI dataset import

**Requirement ID:** REQ-002

### Test Steps:

1. Import a valid DICOM series.
2. Import a file that is not DICOM.

### Expected Result:

The valid series is imported with its slice count; the other file is refused with a reason.

### Automated evidence:

```autotest
UNIT-IMPORT-001
```

## TEST-005: Study intake journey

**Requirement ID:** REQ-T1-001

A Tier 1 journey test: a clinician exercises the whole intake path end to end.

### Test Steps:

1. Sign in as a clinician.
2. Import an MRI study whose identifiers match, then one whose identifiers do not.
3. Open the operator review queue and reject the mismatched mapping.

### Expected Result:

The matching study is listed on the dashboard; the mismatched one stays quarantined until rejected,
and both actions are in the audit log.

### Test Outcome:

```manualtest
```

## TEST-006: Analysis and export journey

**Requirement ID:** REQ-T1-002

A Tier 1 journey test: a clinician analyses an imported study and exports the results.

### Test Steps:

1. Open an imported reference study and run the standard analysis pipeline.
2. Export the results.
3. Compare the export with the study's reference results.

### Expected Result:

The analysis completes without warnings, and the export matches the reference results and names the
source series.

### Test Outcome:

```manualtest
```
