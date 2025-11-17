# Manual Acceptance Tests

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

## TEST-002: Automatic Playwright test

**Requirement ID:** REQ-003
**Requirement ID:** REQ-005

### Test Steps:

1. The `autoplaywright` block runs the Playwright sample located in the `playwright/` directory and captures the video, stdout/stderr, and a 3×3 key frame grid from the test execution.
2. The scripted run intentionally drives a patient-identifier edit workflow to demonstrate the mitigation for **RISK-001**.

### Expected Result:

The test should pass successfully.

### Test Outcome:

```autoplaywright
0001-test-counter
```

## TEST-003: Automatic test

**Requirement ID:** REQ-003

### Test Steps:

1. This `autotest` block demonstrates running a simple pytest command as part of the report.
2. The quick-running suite doubles as a guardrail for **RISK-002** and **RISK-003**; failures block deployments.

### Expected Result:

The test should pass successfully.

### Test Outcome:

```autoplaywright
0001-test-counter
```
```autotest
pytest --disable-warnings -sv tests/test_hello_world.py
```
