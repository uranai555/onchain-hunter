---
name: testing-scoring-pipeline
description: Test onchain-hunter scoring and collector refactors with deterministic data, mocks, regression tests, and exact baseline comparisons.
---

# Testing the scoring pipeline

## Setup

Use Python 3.11 or newer and install development dependencies in the repository-local environment:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

The main validation commands are:

```bash
.venv/bin/python -m pytest tests/test_scoring.py -q
.venv/bin/python -m pytest
.venv/bin/ruff check src/ scripts/ tests/
```

## Scoring refactors

- Build deterministic fill DataFrames with fixed UTC timestamps, prices, sizes, sides, coins, and mixed PnL.
- Verify configuration through `PipelineConfig.from_dict`; use both omitted thresholds and explicit overrides.
- For behavior-preserving changes, load the base-branch implementation from `git show origin/master:<path>` into a separate `types.ModuleType` and compare output DataFrames with `pd.testing.assert_frame_equal(..., check_exact=True)`.
- Use threshold overrides that force concrete score changes rather than only checking score ranges.

## Collector logging

- Do not call the live Hyperliquid endpoint when testing logger routing.
- Patch candidate-wallet loading for progress paths and patch fill fetching with `requests.HTTPError` responses for retry paths.
- Assert logger method, message arguments, retry count, and backoff values.

## Evidence

These tests are shell-only; preserve command output in the test report rather than recording an idle desktop. If testing an open PR, add one PR comment containing runtime assertions and visual CI evidence.

## Devin Secrets Needed

- None for deterministic scoring and mocked collector tests.
- `DUNE_API_KEY` and `FLIPSIDE_API_KEY` are needed only for live provider-backed pipeline testing.
- Optional live integrations may use `NANSEN_API_KEY`, `ARKHAM_API_KEY`, and `GMGN_API_KEY`.
