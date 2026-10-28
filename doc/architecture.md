# Architecture

`datalab_dynamic_demo` uses inward-facing dependencies:

```text
adapters -> workflow -> core
```

- `core` owns host-independent domain behavior and does not import DataLab.
- `workflow` may use DataLab's headless recipe contracts, but not GUI modules.
- `adapters/desktop.py` defines the DataLab plugin class: DataLab loads it from
  the project folder, and the installed entry point targets it.
- `adapters/web.py` records Web support explicitly and starts as unsupported.

The package root exposes identity metadata without importing a host adapter.
`tests/unit/test_architecture.py` checks these boundaries as the project grows.

## Live update

- `core/source.py` produces the simulated frames and `core/regulation.py` decides which frame to compute and which result to publish: both are plain Python, tested without Qt.
- `adapters/executor.py` runs the computations in a one-process pool of its own, through DataLab's `run_with_env`, so that DataLab's processing pool is never interrupted.
- `adapters/controller.py` owns the timers, the worker thread reading the source and the publication of each frame and its result, in the GUI thread.
- `adapters/widgets.py` holds the status bar indicator and the settings popup, which only call the controller.

The workflow layer is not used by the demo (no recipe).
