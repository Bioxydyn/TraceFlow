from setuptools import setup, find_packages


def get_install_requires() -> list[str]:
    return [
        "setuptools",
        "alive-progress",
        "wheel",
        "mistune"
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
    version="0.1.0",
    packages=find_packages(),
    install_requires=get_install_requires(),
    extras_require=get_extras_require(),
    entry_points={
        "console_scripts": [
            "traceflow = traceflow.__main__:main",
        ],
    },
)