#!/bin/bash

set -eux

./py-lint.sh

pytest --disable-warnings -sv tests/
