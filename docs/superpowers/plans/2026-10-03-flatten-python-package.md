# Flatten Python Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `askdb-agent/src/askdb_agent/` and place its Python modules directly under `askdb-agent/src/`.

**Architecture:** Keep the `askdb-agent` project root and distribution name. Flatten only the Python package namespace, update imports and build metadata, and keep the HTTP API and SSE behavior unchanged.

**Tech Stack:** Python 3.11+, FastAPI, Hatchling, uv.

**Spec:** `docs/Agent架构骨架与开发规范.md` and the user-approved layout in this task.

## Global Constraints

- The Python package directory `askdb-agent/src/askdb_agent/` must not remain.
- Preserve the outer `askdb-agent/` project directory and distribution name `askdb-agent`.
- Preserve HTTP endpoints and SSE event behavior.
- Preserve unrelated pre-existing working-tree edits without including them in this commit.

---

### Task 1: Flatten the source tree and update Python imports

**Files:**
- Move: `askdb-agent/src/askdb_agent/*` into `askdb-agent/src/`
- Modify: `askdb-agent/tests/*.py`
- Modify: `askdb-agent/pyproject.toml`

**Interfaces:**
- Preserve the existing public FastAPI app as `main:app` and provide the `askdb-agent` CLI command.

- [x] Move the package contents up one level and remove the now-empty package initializer.
- [x] Replace `askdb_agent.*` imports with their top-level package paths in source and tests.
- [x] Configure Hatchling to package the contents of `src/` at the wheel root and define the CLI entry point.

### Task 2: Update current documentation and commands

**Files:**
- Modify: `askdb-agent/README.md`
- Modify: `askdb-web/README.md`
- Modify: `docs/开发文档.md`
- Modify: `docs/Agent架构骨架与开发规范.md`
- Modify: `docs/Agent记忆体系设计.md`

**Interfaces:**
- Document startup as `uv run uvicorn main:app ...` and admin operations as `uv run askdb-agent auth ...`.

- [x] Update current startup, CLI, source-tree, and code-reference paths.
- [x] Leave archived implementation plans as historical records.

### Task 3: Check the final change and commit

**Files:**
- Review: all files changed by Tasks 1 and 2.

**Interfaces:**
- No endpoint, request, response, or event contract changes.

- [x] Run whitespace and stale-path checks; build the wheel if the local build backend is available.
- [x] Stage only this refactor and its plan; leave pre-existing user edits unstaged.
- [ ] Commit the refactor and push `main` to `origin`.
