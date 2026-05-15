#!/usr/bin/env bash

set -euo pipefail

echo "== Publishability check =="

echo
echo "-- Searching for likely secrets or personal paths"
rg -n --hidden -S \
  -g '!frontend/**' \
  -g '!docs/**' \
  -g '!examples/**' \
  -g '!.git/**' \
  -g '!.venv/**' \
  -g '!venv-iopaint/**' \
  -g '!.claude/**' \
  -g '!lama/**' \
  -g '!sam/**' \
  -g '!outputs/**' \
  -g '!data/**' \
  -g '!pipeline/outputs/**' \
  -g '!app/student_model/qwen_gimp_student_lora_colab/**' \
  -g '!app/student_model/qwen_gimp_student_v6/**' \
  -e 'sk-[A-Za-z0-9]+' \
  -e 'hf_[A-Za-z0-9]+' \
  -e 'ghp_[A-Za-z0-9]+' \
  -e 'github_pat_[A-Za-z0-9_]+' \
  -e '/home/[A-Za-z0-9._-]+' \
  -e 'API_KEY' \
  -e 'SECRET' \
  -e 'TOKEN' \
  . || true

echo
echo "-- Large model and artifact directories present locally"
find . -maxdepth 3 \( \
  -path './.git' -o \
  -path './.venv' -o \
  -path './venv-iopaint' -o \
  -path './lama' -o \
  -path './sam' -o \
  -path './outputs' -o \
  -path './data' -o \
  -path './pipeline' -o \
  -path './Segmentation_Instance' \
\) -print || true

echo
echo "-- Git ignored status preview"
git status --short --ignored

echo
echo "Review the output before staging files."
