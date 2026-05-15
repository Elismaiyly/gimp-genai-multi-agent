# AGENTS.md

## Project overview
This repository is an AI-powered image editing pipeline for GIMP.

Main flow:
1. run_cli.py receives a natural-language instruction
2. StudentGimpAgent generates a JSON editing plan
3. student_postprocess normalizes the plan
4. IRTranslator converts the plan to executor actions
5. GimpExecutor calls the Vision Agent for segmentation
6. mask refinement is applied
7. send_actions_to_gimp.py executes actions in GIMP

## Important files
- run_cli.py: CLI entry point
- app/student_model/student_agent.py: LLM + LoRA generation
- app/student_model/student_postprocess.py: plan normalization
- app/executor/ir_translator.py: IR V3 -> executor translation
- app/executor/gimp_executor.py: segmentation, mask refinement, GIMP action compilation
- vision_agent_a2a.py: vision service
- send_actions_to_gimp.py: bridge to GIMP plugin

## Project rules
- Do not make broad refactors unless asked
- Prefer minimal, local changes
- Preserve helmet handling while improving clothing segmentation
- Always explain which files you inspected before proposing code changes
- When fixing bugs, first identify root cause, then propose the smallest safe patch

