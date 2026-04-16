"""Week 8 smoke-test scripts.

Each file in this directory is a self-contained CLI you can run from any shell
(PowerShell, bash, zsh) to verify one step of the Week 8 runbook. Every script:

- Inserts the repo root on ``sys.path`` so ``analytics.*`` / ``common.*`` imports work
  when invoked as ``python scripts/smoke/<name>.py`` from any CWD.
- Loads the repo-root ``.env`` via ``common.env.load_repo_root_dotenv`` so
  ``PYTHON_DATABASE_URL``, ``LLM_*`` env vars resolve without extra setup.
- Prints structured output so the runbook can show an expected result.
"""
