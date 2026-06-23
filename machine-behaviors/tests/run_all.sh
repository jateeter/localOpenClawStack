#!/usr/bin/env bash
# Run every machine-behaviors test suite. Usage: bash tests/run_all.sh
set -e
cd "$(dirname "$0")/.."
echo "── prototype suite ──"
MB_DEBUG=0 python3.13 tests/run_tests.py
echo ""
echo "── domain sweep suite ──"
MB_DEBUG=0 python3.13 tests/run_domain_tests.py
echo ""
echo "── corpus-wide suite ──"
MB_DEBUG=0 python3.13 tests/run_corpus_tests.py
