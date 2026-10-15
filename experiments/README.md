# Experiments

Put your experiment scripts here: one script per idea, each one a plain Python file using `labrat_cli.Lab`.
API: [`docs/experiments.md`](../docs/experiments.md).

Run one (server and token from `LABRAT_SERVER` / `LABRAT_TOKEN`, default `http://127.0.0.1:8787` / `labrat`):

    cd apps/cli && uv run python ../../experiments/orange_sweep.py

Copy `orange_sweep.py` to start a new one. Its CSV is saved next to the script.
