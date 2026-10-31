# 09 — CI checkout and preparation dependencies

2026-10-30 (America/New_York). Failed run: https://github.com/redthepro/reds-bngearth/actions/runs/41159724780 at `9350656`. Linux test artifacts were downloaded and inspected; the failures were not visible in the local environment because it had outer-package configuration and preparation dependencies.

## Causes and correction

- `test_assetcatalog.py` loaded examples from the checkout's parent `config` directory. It now resolves committed examples under checkout-relative `docs/earth2/config`.
- Two provider contract tests import Rasterio. The regression workflow now installs `server/requirements-preparation.txt` (Rasterio 1.5.2) in addition to runtime requirements. Runtime packaging requirements remain separate.
- Keep `1.8.5-rc.1`: the failed run cannot publish through its failed test gate. No version/tag workaround or weakened test assertions.

## Verification

- Dedicated Python 3.12.14: `python tools/run_tests.py test_assetcatalog test_provider_contract test_soilgrids test_glcfcs10 test_jrcwater --par 2 --logs <state>/09-ci-fix/local`: five modules / 61 tests passed.
- Independent exported snapshot: `git archive HEAD`, extracted under dedicated state, then overlaid only the two patched files. Verified outer `config` directory is absent and committed `docs/earth2/config` exists. Asset catalog 22/22 and provider contract 9/9 passed using the repository runner. Installed Rasterio 1.5.2 matches the CI preparation pin.
- `git diff --check`: passed. Live CI rerun is required to confirm both operating systems; no engine claim.

## Agent ledger

- `/root/ci_portability_review` (earth_explorer, Luna/medium): read-only audit of test path/dependency assumptions; found no further concrete outer-config reference.
- `/root/ci_fix_validation` (earth_validator, Luna/medium): independently exported and tested the isolated snapshot; reports under `%LOCALAPPDATA%/beamng-earth-2/benchmarks/09-ci-fix/independent/run-20261030-01`.
- Lead `/root`: GitHub log/artifact inspection, two-file correction, affected regression checks and delivery. Original checkout and game installations were not touched.
