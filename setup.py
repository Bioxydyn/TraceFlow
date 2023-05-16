from setuptools import setup, find_packages
from pathlib import Path
import re


def get_version(package: str) -> str:
    """
    Return package version as listed in `__version__` in `pipelines/version.py`.
    """
    version = Path(package, "version.py").read_text()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", version).group(1)



def get_install_requires() -> list[str]:
    return [
        "setuptools",
        "wheel",
        "mistune==2.0.5",
        "latex==0.7.0",
        "cairosvg==2.7.0",
        "Jinja2==3.1.2",
    ]


def get_extras_require() -> dict[str, list[str]]:
    return {
        "dev": [
            "flake8",
            "flake8-bugbear",
            "flake8-builtins",
            "flake8-fixme",
            "flake8-walrus",
            "flake8-return",
            "flake8-printf-formatting",
            "flake8-broken-line",
            "flake8-comprehensions",
            "flake8-eradicate",
            "flake8-executable",
            "flake8-bandit",
            "flake8-annotations",
            "setuptools",
            "pytest",
            "mypy",
            "coverage"
        ]
    }


setup(
    name="traceflow",
    version=get_version("traceflow"),
    packages=find_packages(),
    install_requires=get_install_requires(),
    extras_require=get_extras_require(),
    entry_points={
        "console_scripts": [
            "traceflow = traceflow.__main__:main",
        ],
    },
)