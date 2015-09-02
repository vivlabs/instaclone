#!/bin/bash

# Primitive but effective test harness for running command-line regression tests.
# This is all rather ugly but once this harness works, you only need to look at the
# tests file and occasionally edit the cleanup patterns below.

set -euo pipefail
trap "echo && echo 'Tests failed! See failure above.'" ERR

dir="$(cd `dirname $0`; pwd)"
prog_name=exporter

full_log=${1:-$dir/tests-full.log}
clean_log=${2:-$dir/tests-clean.log}


echo Cleaning up...

test_bucket=${TEST_BUCKET:?error: must specify an S3 bucket for tests, in the TEST_BUCKET env variable}
s4cmd del -r s3://$test_bucket/tmp/instaclone-tests

rm -rf "$dir/tmp-dir"
cp -a $dir/work-dir $dir/tmp-dir
cd $dir/tmp-dir

echo "Running..."

# Hackity hack:
# Remove per-run and per-platform details to allow easy comparison.
# Update these patterns as appropriate.
# Note we use perl not sed, so it works on Mac and Linux.
# The $|=1; is just for the impatient and ensures line buffering.
# We also use the cat trick below so it's possible to view the full log as it
# runs on stderr while writing to both logs.
$dir/tests.sh 2>&1 \
  | tee $full_log \
  | tee >(cat 1>&2) \
  | perl -pe '$|=1; s/([a-zA-Z0-9._]+.py):[0-9]+/\1:xx/g' \
  | perl -pe '$|=1; s/File ".*\/([a-zA-Z0-9._]+.py)", line [0-9]*,/File "...\/\1", line __X,/g' \
  | perl -pe '$|=1; s/, line [0-9]*,/, line __X,/g' \
  | perl -pe '$|=1; s/partial.[a-z0-9]*/partial.__X/g' \
  | perl -pe '$|=1; s/ at 0x[0-9a-f]*/ at 0x__X/g' \
  | perl -pe '$|=1; s/[0-9.:T-]*Z/__TIMESTAMP/g' \
  | perl -pe '$|=1; s|s3://[a-zA-Z0-9_-]+/|s3://__BUCKET/|g' \
  | perl -pe '$|=1; s|/s3/[a-zA-Z0-9_-]+/|/s3/__BUCKET/|g' \
  | perl -pe '$|=1; s|/private/tmp/|/tmp/|g' \
  | perl -pe '$|=1; s|/\S+/.instaclone/|__DIR/.instaclone/|g' \
  > $clean_log

echo "Tests done."
echo
echo "Full log: $full_log"
echo "Clean log: $clean_log"
echo
echo "Validation is manual. To compare regression test results with previously correct output, run:"
echo "git diff $clean_log"
