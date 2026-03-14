"""
app/planner/planner_stub.py
===========================

Bridge : call_sml() doit exister pour le wrapper local_sml_agent.py

En v0, on redirige vers planner_llm (Ollama -> IR).
"""

from app.planner.planner_llm import call_sml

__all__ = ["call_sml"]
