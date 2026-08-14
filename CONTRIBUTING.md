# Contributing

## Development setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Change boundaries

- Keep desktop API and scheduling logic in `app_backend.py`.
- Keep site selectors and DOM behavior in the matching platform module.
- Keep `LiepinSearchPage` as the stable public facade; add behavior to the responsible mixin.
- Do not copy source back from `runtime/`, `backups/`, or packaged executables.

## Browser workflow changes

1. Add or update unit tests for pure parsing, configuration, and state logic.
2. Run the full compile and unit-test checks.
3. Verify browser behavior with real sending disabled.
4. Limit the candidate and page count before a controlled real-send test.
5. Redact candidate information, cookies, and API keys from logs and issues.

DOM actions should wait for a verifiable state after navigation or clicking. Avoid treating a dispatched click as proof that the platform accepted an action.
