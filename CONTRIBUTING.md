# Contributing

To try a change in DataLab, add the project folder in **Plugins > Configure
plugins... > Plugin settings** and reload the plugins: no installation is
needed.

Install the project in editable mode and run its checks before submitting a
change:

```bash
python -m pip install -e ".[test]"
python -m pytest
python -m ruff check .
```

Keep domain algorithms in `core`, headless orchestration in `workflow`, and
host-specific behavior in `adapters`. Add focused tests at the same layer as
the behavior under test.
