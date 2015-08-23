#!/bin/bash

# Test script. Output of this script can be saved and compared to test for regressions.
# Double-spacing between commands here makes the script output easier to read.

# We turn on exit on error, so that any status code changes cause a test failure.
set -e -o pipefail

prog_name=instaclone
base_dir=`dirname $0`
config=$base_dir/instaclone.yml
prog=$base_dir/../${prog_name}/main.py

# Run harness to set test config externally.
run() {
  $prog "$@"
}

# A trick to test for error conditions.
expect_error() {
  echo "(got expected error: status $?)"
}

# This will echo all commands as they are read. Bash commands plus their
# outputs will be used for validating regression tests pass (set -x is similar
# but less readable and sometimes not deterministic).
set -v

# --- Start of tests ---

# Python version we're using to run tests.
python -V

run purge

# Error invocations.
run bad_command || expect_error

run configs

# Check contents before.
ls -F -1

head -10 test-dir/* test-file{1,2}

run install || expect_error

run publish

# Check for results of publish.
ls -F -1

readlink test-dir
readlink test-file1

head -10 test-dir/* test-file{1,2}

# Publish again.
run publish || expect_error

run install -f

run purge

# --- End of tests ---
