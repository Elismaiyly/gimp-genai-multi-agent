# Repository Structure Proposal

## Files Inspected

This proposal was prepared after inspecting:

- `README.md`
- `pyproject.toml`
- `run_cli.py`
- `app/student_model/student_agent.py`
- `app/executor/gimp_executor.py`
- `vision_agent_a2a.py`
- `send_actions_to_gimp.py`
- `gimp-mcp-plugin.py`

## Current State

The repository mixes:

- production-relevant pipeline code
- training scripts
- local experiments
- model checkpoints
- generated outputs
- documentation

That is common for an active research project, but it makes public presentation less clear.

## Recommended Public Structure

```text
repo/
├── backend/
│   ├── api/
│   ├── orchestration/
│   └── transport/
├── agents/
│   ├── student/
│   ├── translator/
│   ├── executor/
│   └── dialog/
├── vision-service/
│   ├── api/
│   ├── segmentation/
│   ├── refinement/
│   └── inpainting/
├── gimp-plugin/
│   ├── plugin/
│   └── bridge/
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── docs/
├── examples/
├── scripts/
├── tests/
├── pyproject.toml
└── README.md
```

## Mapping From Current Repository

### Backend Python

- `run_cli.py`
- `send_actions_to_gimp.py`
- `gimp_mcp_server.py`

Target destination:

- `backend/orchestration/`
- `backend/transport/`

### Agents

- `app/agents/`
- `app/dialog/`
- `app/student_model/`
- `app/planner/`
- `app/nlu/`

Target destination:

- `agents/student/`
- `agents/translator/`
- `agents/executor/`
- `agents/dialog/`

### Vision Service

- `vision_agent_a2a.py`
- `app/vision/`
- vision-related support inside `app/executor/`

Target destination:

- `vision-service/`

### GIMP Plugin

- `gimp-mcp-plugin.py`

Target destination:

- `gimp-plugin/plugin/`

### Frontend

- new static demo in `frontend/`

### Docs

- `docs/`
- architecture and publication notes

### Scripts

- publication checks
- setup helpers
- run helpers

### Examples

- prompt examples
- sample plans
- screenshots

### Tests

Current tests are under `app/tests/`.

Target destination:

- `tests/unit/`
- `tests/integration/`

## Migration Strategy

To avoid breaking the current pipeline, the recommended migration is incremental:

1. Keep runtime files where they are today.
2. Add GitHub-facing documentation and frontend now.
3. Introduce new top-level directories as presentation and organization layers.
4. Move runtime code only after path/config cleanup and import stabilization.

## Immediate Safe Actions

- keep the current execution paths unchanged
- improve README and repo hygiene
- add publishability checks
- hide large artifacts and local environments with `.gitignore`
- add a standalone demo frontend

## Deferred Refactors

These should happen only when you are ready to update imports and paths:

- remove hardcoded local filesystem paths
- split training assets from runtime assets
- move tests to a top-level `tests/` tree
- create configuration modules for ports, model paths, and sample images
