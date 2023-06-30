# Requirements

These are some general notes on this set of requirements. Other content within subsections are for specific requirements.

## REQ-001: User authentication

Users must be able to authenticate using their email address and a password.

### Example LaTeX equation

The strength of the user's password should follow the entropy equation:

$$ H = L \times \log_2(N) $$

Where $H$ is the entropy, $L$ is the password length, and $N$ is the number of possible symbols.

### Example inline code formatting

The ID of the user is, e.g., `1af345e6`.

## REQ-002: MRI dataset import

The platform must support importing MRI datasets in DICOM format.

### Example image (.png)

![VoxelFlow Logo](./voxelflow-logo.png)

## REQ-003: Image analysis pipeline

- The platform should provide a Python API for building image analysis pipelines.
- The pipelines must be able to process MRI datasets in a compliant way.

### Example flow chart (using mermaid)

```mermaid
graph LR
A[Preprocessing] --> B[Segmentation]
B --> C[Feature extraction]
C --> D[Classification]
```

### Example code block

```python
def hello_world():
    print("Hello world!")
```

## REQ-004: Output data

The platform must be able to export the results of image analysis pipelines in a standard format, including the following parameters:


| **DICOM Series**       | **Key Parameters**              | **Typical Parameters**                                        | **Map to Image Type** |
|------------------------|---------------------------------|---------------------------------------------------------------|-----------------------|
| T1 VFA                 | TR, TE, FA                      | 96x96x24<br><br>FA=2°,17°,32°                                 | `vfa`                 |
| High-res pre-contrast  | TR, TE, FA                      | 512x512x92<br><br>FA=32°                                      | `high-res-pre`        |
| T1-weighted dynamic    | TR, TE, FA, Temporal resolution | 96x96x24 <br><br>FA=17°<br><br>Temporal resolution: 2s to 10s | `dynamic-uncorrected` |
| High-res post-contrast | TR, TE, FA                      | 512x512x92<br><br>FA=32°                                      | `high-res-post`       |

