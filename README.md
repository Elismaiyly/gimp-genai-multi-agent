# GIMP GenAI Multi-Agent Pipeline

[![Python](https://img.shields.io/badge/Python-3.11+-1f6feb.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Service-05998b.svg)](https://fastapi.tiangolo.com/)
[![GIMP](https://img.shields.io/badge/GIMP-3.0-orange.svg)](https://www.gimp.org/)
[![Transformers](https://img.shields.io/badge/Hugging%20Face-Transformers-f59e0b.svg)](https://huggingface.co/docs/transformers/index)

Natural-language image editing for GIMP, powered by a multi-agent GenAI pipeline. The system translates a user instruction into a structured plan, validates it, calls vision services for segmentation, and executes the final actions inside GIMP.

This repository is being prepared as a clean GitHub portfolio version: the runtime pipeline stays intact, while documentation, repository hygiene, examples, and a presentation frontend are added around it.

## Project Summary

The project turns prompts such as:

```text
"Change the rider's jacket to red and blur the background."
```

into an execution chain:

```text
User
  -> StudentGimpAgent
  -> TranslatorAgent
  -> ExecutorAgent
  -> Vision Agent
  -> GIMP Plugin
  -> Final Image
```

It combines LLM-based planning, IR normalization, object-aware segmentation, mask refinement, and GIMP action execution through local TCP/JSON and HTTP/JSON communication.

## Architecture

### Current Runtime Components

- `run_cli.py`: CLI orchestrator for the end-to-end demo pipeline.
- `app/student_model/student_agent.py`: student LLM agent that emits structured JSON editing plans.
- `app/student_model/student_postprocess.py`: plan normalization and recovery rules.
- `app/agents/translator_agent.py`: converts normalized plan messages into executor-oriented IR.
- `app/executor/ir_translator.py`: IR V3 translation and validation logic.
- `app/agents/executor_agent.py`: bridges translated IR to executor actions.
- `app/executor/gimp_executor.py`: calls vision, refines masks, and compiles GIMP actions.
- `vision_agent_a2a.py`: vision service and A2A endpoint.
- `send_actions_to_gimp.py`: sends JSON actions to the GIMP plugin.
- `gimp-mcp-plugin.py`: plugin side inside GIMP.

### Repository Structure Target

The current codebase is functional but still research-oriented. The recommended GitHub-facing structure is documented in [docs/repository-structure.md](docs/repository-structure.md) and separates:

- `backend/`: Python API and orchestration layer
- `agents/`: planning, translation, executor, and dialog agents
- `vision-service/`: detection, segmentation, inpainting, A2A endpoints
- `gimp-plugin/`: plugin-side transport and execution bridge
- `frontend/`: demo UI
- `docs/`: architecture, setup notes, publication checklist
- `examples/`: prompts, sample JSON, screenshots
- `scripts/`: helper utilities
- `tests/`: automated validation

For now, this repository keeps the existing paths to avoid breaking imports and local execution.

## Pipeline

1. The user writes a natural-language editing command.
2. `StudentGimpAgent` produces a structured JSON response.
3. `student_postprocess` normalizes ambiguous or incomplete plans.
4. `TranslatorAgent` and IR translation convert the plan into executable actions.
5. `GimpExecutor` requests object localization and masks from the vision agent.
6. Mask refinement is applied before action compilation.
7. Actions are sent to the GIMP plugin through JSON over TCP.
8. GIMP executes the edits and returns the result status.

## Features

- Natural-language control of GIMP editing workflows
- Multi-agent orchestration from prompt to executable action plan
- Structured JSON planning and normalization
- Vision-guided object segmentation and mask refinement
- GIMP plugin integration through local transport
- Extensible protocol layers using MCP and A2A concepts
- Research-friendly architecture for training and evaluating planning agents

## Technology Stack

- Python
- FastAPI
- PyTorch
- Hugging Face Transformers
- PEFT / LoRA
- YOLOv8
- SAM
- OWL-ViT
- LaMa
- GIMP Python API
- TCP/JSON
- HTTP/JSON
- MCP
- A2A

## Repository Layout

```text
.
├── app/                    # Current Python application code
├── docs/                   # GitHub-facing documentation
├── examples/               # Prompt and JSON examples
├── frontend/               # Static demo UI
├── scripts/                # Publication and utility scripts
├── run_cli.py              # Main local CLI demo
├── vision_agent_a2a.py     # Vision service
├── gimp-mcp-plugin.py      # GIMP plugin
└── send_actions_to_gimp.py # GIMP transport bridge
```

## Installation

### Prerequisites

- Python `3.11+`
- GIMP `3.x`
- A local environment capable of running the vision stack
- Access to the required model weights outside Git tracking

### Local Setup

```bash
git clone <your-repository-url>
cd gimp-mcp-backup
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Additional runtime dependencies for the full vision stack may need to be installed separately depending on your local setup and model choices.

## Running the Project

### 1. Start the vision service

```bash
python vision_agent_a2a.py
```

### 2. Ensure the GIMP plugin is installed

Install `gimp-mcp-plugin.py` in your local GIMP plug-ins directory, then launch GIMP.

### 3. Start the CLI demo

```bash
python run_cli.py
```

### 4. Open the demo frontend

```bash
python -m http.server 8080 --directory frontend
```

Then open `http://localhost:8080`.

## Example Commands

Examples are also collected in [examples/commands.md](examples/commands.md).

```text
Change the rider's jacket to red.
Remove the helmet from the subject.
Blur the background while keeping the person sharp.
Replace the shirt color with dark blue.
Desaturate the full image and sharpen the main object.
```

## Example JSON Plan

Examples are also collected in [examples/sample_plan.json](examples/sample_plan.json).

```json
{
  "mode": "plan",
  "plan": {
    "actions": [
      {
        "action": "object.recolor",
        "params": {
          "object": "jacket",
          "color": "red"
        }
      }
    ]
  }
}
```

## Screenshots and Results

Portfolio assets can be placed in `docs/assets/` or `examples/results/` and linked here.

Suggested captures:

- input image before editing
- generated plan JSON
- segmentation mask preview
- GIMP result after execution
- frontend demo screen

The current frontend already includes a placeholder showcase layout for before/after comparison and JSON visualization.

## Frontend Demo

The static demo UI in `frontend/` is designed for GitHub portfolio presentation. It includes:

- landing page with project positioning
- command input panel
- before/after preview cards
- generated JSON panel
- visible pipeline stages

It is intentionally decoupled from the current runtime so it does not interfere with the existing Python pipeline.

## Documentation

- [docs/repository-structure.md](docs/repository-structure.md): recommended repository organization
- [docs/publishing-checklist.md](docs/publishing-checklist.md): pre-publication hygiene checklist

## Publication Safety

Before pushing to GitHub, run:

```bash
bash scripts/check_publishability.sh
```

This scans for common risks such as:

- hardcoded personal paths
- API key patterns
- local environment files
- heavy checkpoints and generated artifacts

## Roadmap

- Externalize hardcoded local paths into environment-based configuration
- Add a proper FastAPI backend entrypoint for the full multi-agent pipeline
- Expose the demo frontend to the live pipeline
- Add reproducible Docker-based setup for vision services
- Publish curated examples and benchmark tasks
- Expand automated tests for segmentation and plugin integration
- Separate research assets from product-facing source tree

## Notes

- Heavy model weights, checkpoints, virtual environments, local outputs, and private datasets should not be committed.
- Several research and local utility scripts still exist in the repository; they should be curated progressively rather than moved blindly.
- The current portfolio cleanup intentionally avoids changing the core runtime layout to prevent regressions.
