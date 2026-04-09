# Coding Standards

## General Principles

- Write clear, readable code over clever code.
- Each function / method should do **one thing** well (Single Responsibility Principle).
- Prefer explicit over implicit.

## Python

- Follow [PEP 8](https://peps.python.org/pep-0008/) for style.
- Use type hints for all public functions.
- Keep functions under 40 lines where possible.
- Write docstrings for every public module, class, and function.
- Use `pytest` for unit tests; aim for ≥ 80 % line coverage.

## Git & Pull Requests

- Branch names: `feature/<short-description>`, `fix/<short-description>`.
- PR title must summarise the change in ≤ 72 characters.
- Every PR needs at least one approval before merging.
- Squash-merge to keep a clean linear history on `main`.
- Reference the Jira ticket in the PR description (e.g. `Closes PROJ-123`).

## Code Review Checklist

- [ ] Does the code follow the coding standards above?
- [ ] Are there unit tests for new or changed logic?
- [ ] Are error cases handled gracefully?
- [ ] Is sensitive data (secrets, PII) excluded from logs?
- [ ] Are dependencies up-to-date and free of known CVEs?
