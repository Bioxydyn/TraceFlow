# TraceFlow

![TraceFlow Logo](./traceflow/res/traceflow-logo.png)

TraceFlow is a Python library designed to help manage software development documentation and testing processes, streamlining the production of requirements, design documents, and test cases (both automated and manual). With built-in support for PDF export, you can easily sign the output making TraceFlow particularly suited for projects in regulated environments or environments where compliance with 21 CFR 11 is necessary.

By leveraging TraceFlow, you can:

- Maintain your documentation in the same git repository as your code, ensuring version control and easy collaboration.
- Automatically produce a "validation pack" containing all requirements, design, and test plans in PDF format.
- Generate a traceability matrix linking all requirements to all tests.
- Create fillable PDF forms for manual tests.
- Run automated tests and capture their output as Markdown, then include them in the PDF report.

## Getting Started

To use TraceFlow, organize your Markdown files in the following folder structure:

```
project/
  ├── requirements/
  │   ├── requirements.md
  │   └── ...
  ├── design/
  │   ├── design.md
  │   └── ...
  └── tests/
      ├── test_plan.md
      └── ...
```
Create your Markdown files based on the provided examples:

 - [Requirements Document Example](#requirements)
 - [Test Plan Example with Manual Test](#test-plan)
 - [Design Document Example](#design-document)

### Running TraceFlow

With your Markdown files in place, you can run TraceFlow using the following command:

    traceflow path/to/project-docs project.pdf

This command will generate a PDF for all of your documentation and test cases, as well as a validation pack and traceability matrix.

### Requirements
```
# Requirements

## REQ-001: User authentication

Users must be able to authenticate using their email address and a password.

### Example LaTeX equation

The strength of the user's password should follow the entropy equation:

$$ H = L \times \log_2(N) $$

Where $H$ is the entropy, $L$ is the password length, and $N$ is the number of possible symbols.

## REQ-002: MRI dataset import

The platform must support importing MRI datasets in DICOM format.

### Example image (.png)

![Caption for example image](./mri_sample.png)

## REQ-003: Image analysis pipeline

- The platform should provide a Python API for building image analysis pipelines.
- The pipelines must be able to process MRI datasets in a compliant way.

### Example table

| Step          | Description                                      | API Function       |
|---------------|--------------------------------------------------|--------------------|
| Preprocessing | Remove noise, artifacts, and normalize intensity | preprocess_data()  |
| Segmentation  | Segment the relevant regions of interest         | segment_roi()      |
| Feature extraction | Extract features from segmented regions      | extract_features() |
| Classification | Classify the extracted features                 | classify_data()    |

### Example flow chart (using mermaid)

\```mermaid
graph LR
A[Preprocessing] --> B[Segmentation]
B --> C[Feature extraction]
C --> D[Classification]
\```

```

### Test Plan
```
## TEST-001: User authentication

**Requirement ID:** REQ-001

### Test Steps:

1. Navigate to the login page.
2. Enter a valid email address and password.
3. Click the "Login" button.

### Expected Result:

The user is logged in and redirected to the main dashboard.

### Test Result (Manual):

\```manualtest
\```
```

### Design Document
```
## User Authentication

To address **REQ-001**, we will implement an authentication system using JWT (JSON Web Tokens). The system will include the following components:

- A login page with input fields for email and password
- A backend API endpoint to validate user credentials
- Middleware to validate JWT tokens for accessing protected resources

## MRI Dataset Import

To address **REQ-002**, we will develop a module to import MRI datasets in DICOM format. The module will include:

- A function to parse DICOM files
- Error handling for unsupported or malformed files
- Integration with the existing data storage system

## Image Analysis Pipeline

To address **REQ-003**, we will create a Python API for building and executing image analysis pipelines. This API will include:

- A set of Python classes to represent pipeline components
- Functions to connect and execute pipeline components
- Compliance checks to ensure the pipeline adheres to the required standards
```
