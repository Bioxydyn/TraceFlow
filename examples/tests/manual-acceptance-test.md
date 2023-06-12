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

## TEST-002: Automatic test

**Requirement ID:** REQ-003

### Test Steps:

1. This automated test creates a user, logs in, and then logs out.

### Expected Result:

The test should pass successfully.

### Test Outcome:

```autotest
pytest --disable-warnings -sv tests/test_hello_world.py
```