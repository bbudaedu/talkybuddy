# The streaming tests require pytest-asyncio + the pipecat stack, which live in
# the A2 spike venv (talkybuddy/spike/a2_pipecat/.venv), not the main .venv.
# When collected by a venv that lacks pytest-asyncio, skip this directory
# cleanly instead of erroring during collection, so `pytest` in the main venv
# still runs the rest of the suite. Run these tests with the spike venv (see
# run_tests.sh).
try:
    import pytest_asyncio  # noqa: F401
except ImportError:  # pragma: no cover - depends on which venv collects us
    collect_ignore_glob = ["*"]
