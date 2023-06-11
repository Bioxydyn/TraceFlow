#!/bin/bash

set -eux

./py-lint.sh

pytest --disable-warnings -sv tests/

mypy traceflow --ignore-missing-imports
mypy tests --ignore-missing-imports
