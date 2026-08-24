# Security

## Local secrets

- Never commit `DEEPSEEK_API_KEY`, `COMPANY_MODEL_API_KEY`, browser profiles, cookies, `runtime/`, or exported candidate data.
- Put API credentials in `runtime/.env` or enter them locally in the desktop UI.
- Treat `runtime/liepin_web_config.json` as sensitive because it can contain API credentials, model settings and recruitment criteria.
- If a key is ever committed, revoke it before removing it from Git history.

## Local service

The desktop backend is intended to listen on `127.0.0.1`. Do not expose it on a LAN or public interface. Its local API has no authentication because it is designed for the bundled desktop window only.

## Real candidate actions

Browser automation can send messages and request contact details. Keep real sending disabled while developing selectors or workflow logic. Validate changes with a small candidate limit before enabling production actions.

## Reporting

Do not include candidate resumes, cookies, access tokens, or API keys in public issues. Provide redacted logs and a minimal reproduction instead.
