# Backend

Target area for the Python backend and orchestration layer.

Current runtime files still live at the repository root to avoid breaking the existing pipeline:

- `run_cli.py`
- `send_actions_to_gimp.py`
- `gimp_mcp_server.py`

Recommended future split:

- `backend/api/`
- `backend/orchestration/`
- `backend/transport/`
