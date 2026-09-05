"""Test package.

Marked as a package so shared fixtures — the mock Denon receivers in
test_denon.py — can be imported by other test modules. Without it the import
resolves locally (where the container's working directory happens to be on the
path) but not in CI, which is exactly the kind of difference that makes a green
local run meaningless.
"""
