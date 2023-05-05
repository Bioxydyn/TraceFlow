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

![MRI Sample](./mri_sample.png)

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

```mermaid
graph LR
A[Preprocessing] --> B[Segmentation]
B --> C[Feature extraction]
C --> D[Classification]
```
