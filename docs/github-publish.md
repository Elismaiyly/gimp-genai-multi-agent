# GitHub Publishing Commands

Replace the placeholders before running:

```bash
git init
git branch -M main
git add README.md .gitignore docs frontend examples scripts backend agents vision-service gimp-plugin tests pyproject.toml run_cli.py vision_agent_a2a.py send_actions_to_gimp.py gimp-mcp-plugin.py app
git status
bash scripts/check_publishability.sh
git commit -m "Prepare professional GitHub portfolio version"
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

If the repository is already initialized:

```bash
git status
bash scripts/check_publishability.sh
git add .
git commit -m "Prepare professional GitHub portfolio version"
git push origin <your-branch>
```

Recommended first push discipline:

1. Stage intentionally, not blindly.
2. Review ignored files and large artifacts.
3. Confirm no checkpoints, datasets, or local outputs are included.
