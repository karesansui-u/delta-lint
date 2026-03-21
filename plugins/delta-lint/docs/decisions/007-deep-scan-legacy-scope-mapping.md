# 007 — Legacy `scan_type=deep` maps to `scope=smart` in coverage matrix

## Status

Resolved (dl-b7291310).

## Context

`append_scan_history()` infers `scope="smart"` when `scan_type` is `deep` and no explicit `scope` is passed (see `findings.py`). `compute_coverage_matrix()` must use the same axes for history rows that omit `scope`, via `_SCAN_TYPE_TO_AXES`.

Previously, `_SCAN_TYPE_TO_AXES["deep"]` used `scope="wide"`, so the same logical scan could be counted under different cells depending on whether the record included an explicit `scope` field.

## Decision

`_SCAN_TYPE_TO_AXES["deep"]` uses `scope="smart"` to match `append_scan_history()` inference. Wide + deep runs from `cmd_scan` continue to record `scope="wide"` explicitly, so they still aggregate to the wide column.

## Regression

`scripts/tests/test_coverage_scan_history.py` asserts a legacy line `{"scan_type":"deep"}` (no `scope`) increments `(smart, deep, default)` and not `(wide, deep, default)`.
