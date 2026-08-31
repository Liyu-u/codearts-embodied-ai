# Task 1 Report

## Changes

- Added `demo/cloud/auth.py` with:
  - `hash_password(password) -> tuple[bytes, bytes]` using PBKDF2-HMAC-SHA256 with 310000 iterations and a 16-byte random salt.
  - `verify_password(password, salt, digest) -> bool` using `hmac.compare_digest`.
  - `normalize_email(email) -> str` using trim + lowercase normalization.
  - `validate_registration(username, email, password) -> None` with username, email, and password validation.
  - `new_session_token() -> str` using `secrets.token_urlsafe(32)`.
  - `session_token_hash(token) -> bytes` using SHA-256.
- Added `demo/cloud/credentials.py` with `CredentialCipher` backed by `cryptography.fernet.Fernet`.
  - `from_env(production: bool = True)` reads `CLOUD_CREDENTIALS_KEY`.
  - Production mode raises if the key is missing.
  - `encrypt(value: str) -> bytes` and `decrypt(value: bytes) -> str` round-trip through Fernet.
- Updated `requirements.txt` to include `cryptography>=42,<45`.
- Added tests:
  - `tests/unit/test_auth_primitives.py`
  - `tests/unit/test_credentials.py`

## Verification

- Initial red check:
  - `py -3 -m pytest tests/unit/test_auth_primitives.py tests/unit/test_credentials.py -q`
  - Output before installing test dependencies: `C:\Users\14810\AppData\Local\Programs\Python\Python313\python.exe: No module named pytest`
- Dependency install:
  - `py -3 -m pip install "cryptography>=42,<45" pytest`
  - Output included:
    - `Successfully installed cryptography-44.0.3 iniconfig-2.3.0 pluggy-1.6.0 pygments-2.21.0 pytest-9.1.1`
- Focused tests:
  - `py -3 -m pytest tests/unit/test_auth_primitives.py tests/unit/test_credentials.py -q`
  - Output: `.....                                                                    [100%]`
  - Output: `5 passed in 0.57s`
- Bytecode check:
  - `py -3 -m py_compile demo/cloud/auth.py demo/cloud/credentials.py`
  - Output: no output, exit code 0

## Concerns

- The Python 3.13 environment used for validation did not have `pytest` installed initially, so I installed `pytest` and `cryptography` locally to complete the required checks.
- `CredentialCipher.from_env(production=True)` intentionally fails fast when `CLOUD_CREDENTIALS_KEY` is missing, which matches the brief and avoids plaintext fallback.
