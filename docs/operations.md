# Operations

## Running the test suite locally

`pytest-homeassistant-custom-component` is extracted from a specific core
release, sometimes a beta, and pulls that exact core version in as a
dependency. Installing it in the same resolver pass as
`requirements_test.txt` can produce a version conflict, because
`requirements_test.txt` pins the stable `homeassistant` release. Install in
two steps so the harness installs first and the stable pin re-resolves it
afterward:

```bash
pip install pytest-homeassistant-custom-component==0.13.362
pip install -r requirements_test.txt
python -m pytest tests/ -q
```

## Windows development note

`pytest-homeassistant-custom-component` imports `fcntl`, which is
Unix-only. Run the test suite from WSL (or another Linux/macOS
environment) on a Windows development machine; it cannot run under the
native Windows Python.
