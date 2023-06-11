#!/bin/bash

set -eux

flake8 traceflow --exclude=*/cpp/*,.svn,CVS,.bzr,.hg,.git,__pycache__,.tox,.eggs,*.egg --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 tests --count --select=E9,F63,F7,F82 --show-source --statistics

flake8 traceflow --exclude=*/cpp/*,.svn,CVS,.bzr,.hg,.git,__pycache__,.tox,.eggs,*.egg --count --max-complexity=60 --ignore=S101,E203,W503,ANN101,ANN204 --max-line-length=120 --statistics
flake8 tests --count --max-complexity=60 --ignore=S101,E203,W503,ANN101,ANN204 --max-line-length=120 --statistics
