# Manual & Automatic Tests

These are some general notes on this set of test. Other content within subsections are for specific tests.

```testcoverpage
```

## TEST-001: User authentication

**Requirement ID:** REQ-001
**Requirement ID:** REQ-002

### Test Steps:

1. Navigate to the login page.
2. Enter a valid email address and password.
3. Click the "Login" button.

### Expected Result:

The user is logged in and redirected to the main dashboard.

### Test Outcome:

```manualtest
```

## TEST-002: Identifier reconciliation workflow

**Requirement ID:** REQ-003
**Requirement ID:** REQ-005

### Test Steps:

1. Open the patient identifier reconciliation workflow.
2. Trigger an identifier mismatch and verify the application quarantines the record.
3. Confirm the mismatch appears in the operator review queue.

### Expected Result:

The mismatch is quarantined and visible for operator review.

### Test Outcome:

```autotest
TEST-002
```

## TEST-003: Pipeline validation evidence

**Requirement ID:** REQ-003

### Test Steps:

1. Execute the release pipeline checks for preprocessing, segmentation, and classification.
2. Review the output evidence and attach the run ID to the release checklist.
3. Record pass or fail status in this manual section.

### Expected Result:

The pipeline checks complete successfully and evidence is attached to the release record.

### Test Outcome:

```manualtest
```
