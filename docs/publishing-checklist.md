# Publishing Checklist

## Before the First Push

- Review `git status` and confirm only intentional files are staged.
- Run `bash scripts/check_publishability.sh`.
- Verify that no model weights or checkpoints are included.
- Verify that no local virtual environments are included.
- Verify that no personal paths remain in public-facing docs or examples.
- Verify that screenshots do not expose private data.

## Sensitive Content To Exclude

- `.env` files
- API keys and tokens
- local absolute paths such as `/home/...`
- model weights: `*.pt`, `*.pth`, `*.bin`, `*.safetensors`, `*.ckpt`
- checkpoints and training outputs
- temporary images and generated masks
- local datasets and private samples

## Known Current Risks Found During Inspection

- hardcoded personal paths exist in several local scripts
- local image paths exist in CLI/demo scripts
- model directories and generated outputs exist in the repository tree
- virtual environments and caches are present locally

These are now covered by the improved `.gitignore`, but they still require manual staging discipline before the first public push.

## Recommended Public Commit Scope

- source code required to understand the architecture
- tests that do not depend on private assets
- docs and examples
- static frontend demo
- lightweight sample JSON and prompt files

## Recommended Exclusions

- large model folders
- local datasets
- ad hoc experimental scripts if they are not useful to readers
- generated outputs and benchmarks that are not curated
