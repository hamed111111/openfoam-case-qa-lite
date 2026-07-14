# OpenFOAM Case QA Lite

A dependency-free static scanner for catching common OpenFOAM case handover defects before solver time or client review.

## What it checks

- core case structure and required dictionaries;
- time-control sanity in `controlDict`;
- expected discretization and solver-control sections;
- mesh patch inventory and suspicious `defaultFaces`;
- initial-field dimensions, internal fields, and patch coverage;
- presence of common physical-property dictionaries;
- portability risk from included dictionaries.

It produces JSON for automation and a standalone HTML report for review.

## Quick start

Python 3.10+ is sufficient; OpenFOAM is not required for the static scan.

```bash
python3 openfoam_case_audit.py /path/to/case \
  --json audit.json \
  --html audit.html
```

Exit code `0` means no critical static finding. Exit code `2` means the case is blocked by a critical finding. Add `--strict` to make warnings fail CI as well.

## Test

```bash
python3 -m unittest discover -s tests -v
```

## Important limit

This is an early-warning tool, not a physics validator. A clean report does not prove mesh quality, numerical convergence, conservation, correct boundary conditions, valid material data, or agreement with experiments. A qualified engineer must review those items and run the appropriate OpenFOAM utilities and solver.

## Commercial audit service

For a human-reviewed case audit with a prioritized repair memo, the companion service is offered as a **48-hour fixed-price sprint for EUR 450**.

The sprint includes a focused review of the supplied case, models, boundary conditions, solver controls, mesh/convergence evidence, and reproducibility; a Blocker/Major/Minor/Observation repair memo; one 30-minute readout; and one clarification round. It does not include a full production rerun, CAD/mesh rebuilding, certification, regulatory approval, or safety-critical sign-off.

[Open a case-audit inquiry](https://github.com/hamed111111/openfoam-case-qa-lite/issues/new?template=case-audit-interest.md&title=Case%20audit%20inquiry%3A%20%5Bshort%20name%5D) with a short, non-confidential summary. **Do not attach proprietary case files, logs, credentials, or client data to a public issue.** A private transfer method can be agreed after scope fit is confirmed.

## License

MIT License. See `LICENSE`.
