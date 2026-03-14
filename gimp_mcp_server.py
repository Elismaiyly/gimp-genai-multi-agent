#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GIMP MCP Server Script
Pipeline :
    Client MCP (Python, Claude, Inspector, etc.)
        ⇄ FastMCP (ce fichier, transport=stdio)
        ⇄ Plugin GIMP (TCP localhost:9877, JSON)

Le client ne parle JAMAIS directement à GIMP.
Toute la communication GIMP passe par GimpConnection (socket TCP).
"""

from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import logging
import base64
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# 🔧 Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("GimpMCPServer")


# ---------------------------------------------------------------------------
# 🔌 Connexion TCP au plugin GIMP
# ---------------------------------------------------------------------------
class GimpConnection:
    """
    Connexion bas-niveau vers le plugin GIMP.
    Par défaut, le plugin GIMP écoute sur localhost:9877 et parle en JSON.
    """

    def __init__(self, host: str = "localhost", port: int = 9877):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None

    def connect(self):
        """Ouvre une socket TCP vers le plugin GIMP si nécessaire."""
        if self.sock:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to GIMP plugin at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to GIMP plugin: {e}")
            raise ConnectionError(
                "Could not connect to GIMP plugin. "
                "Assure-toi que le plugin MCP dans GIMP écoute bien sur 9877."
            )

    def send_command(self, command_type: str, params: dict | None = None) -> dict:
        """
        Envoie une commande JSON au plugin GIMP et retourne la réponse JSON.

        Le protocole est :
            { "type": "get_gimp_info", "params": {...} }

        On lit jusqu'à ce qu'un JSON complet soit décodable.
        """
        if not self.sock:
            self.connect()

        if params is None:
            params = {}

        # Cas particulier pour apply_filter : le plugin attend déjà filter_type/params
        if command_type == "apply_filter":
            command = {"type": command_type, "params": params}
        else:
            command = {"type": command_type, "params": params}

        try:
            payload = json.dumps(command).encode("utf-8")
            # On peut ajouter un '\n' si ton plugin l'exige, sinon brut :
            # payload += b"\n"
            self.sock.sendall(payload)

            # Réception en morceaux jusqu'à JSON complet
            response_data = b""
            while True:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                response_data += chunk
                try:
                    # Test si JSON complet
                    json.loads(response_data.decode("utf-8"))
                    break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

            # On ferme la socket après chaque commande pour éviter
            # les états bizarres CLOSE_WAIT côté serveur
            self.sock.close()
            self.sock = None

            return json.loads(response_data.decode("utf-8"))

        except Exception as e:
            logger.error(f"Communication error with GIMP plugin: {e}")
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
            self.sock = None
            raise RuntimeError(f"Error communicating with GIMP: {e}")


# Connexion globale Lazy
_gimp_connection: GimpConnection | None = None


def get_gimp_connection() -> GimpConnection:
    global _gimp_connection
    if _gimp_connection is None:
        _gimp_connection = GimpConnection()
    return _gimp_connection


# ---------------------------------------------------------------------------
# 🚀 MCP server (FastMCP)
# ---------------------------------------------------------------------------
mcp = FastMCP("GimpMCP", description="GIMP integration through MCP")


# ---------------------------------------------------------------------------
# 🖼️ Outil : get_image_bitmap
# ---------------------------------------------------------------------------
@mcp.tool()
def get_image_bitmap(
    ctx: Context,
    max_width: int | None = None,
    max_height: int | None = None,
    region: dict | None = None,
) -> Image:
    """
    Récupère l'image courante dans GIMP comme Image MCP (PNG en mémoire).
    """
    try:
        print("Requesting current image bitmap from GIMP plugin...")

        conn = get_gimp_connection()

        params: dict = {}
        if max_width is not None:
            params["max_width"] = max_width
        if max_height is not None:
            params["max_height"] = max_height
        if region is not None:
            params["region"] = region

        result = conn.send_command("get_image_bitmap", params)

        if result.get("status") == "success":
            image_info = result["results"]
            base64_data = image_info["image_data"]
            png_bytes = base64.b64decode(base64_data)

            # Image MCP : data = bytes, format = "png"
            return Image(data=png_bytes, format="png")
        else:
            raise RuntimeError(f"GIMP error: {result.get('error', 'Unknown error')}")

    except Exception as e:
        traceback.print_exc()
        raise RuntimeError(f"Failed to get image bitmap: {e}")


# ---------------------------------------------------------------------------
# 🧾 Outil : get_image_metadata
# ---------------------------------------------------------------------------
@mcp.tool()
def get_image_metadata(ctx: Context) -> dict:
    """
    Récupère les métadonnées de l'image courante (dimensions, couches, etc.).
    Plus léger que get_image_bitmap.
    """
    try:
        print("Requesting current image metadata from GIMP plugin...")

        conn = get_gimp_connection()
        result = conn.send_command("get_image_metadata")

        if result.get("status") == "success":
            return result["results"]
        else:
            raise RuntimeError(f"GIMP error: {result.get('error', 'Unknown error')}")

    except Exception as e:
        traceback.print_exc()
        raise RuntimeError(f"Failed to get image metadata: {e}")


# ---------------------------------------------------------------------------
# ℹ️ Outil : get_gimp_info
# ---------------------------------------------------------------------------
@mcp.tool()
def get_gimp_info(ctx: Context) -> dict:
    """
    Infos complètes sur l'installation GIMP (version, dossiers, PDB, etc.).
    """
    try:
        print("Requesting GIMP environment info from plugin...")

        conn = get_gimp_connection()
        result = conn.send_command("get_gimp_info")

        if result.get("status") == "success":
            return result["results"]
        else:
            raise RuntimeError(f"GIMP error: {result.get('error', 'Unknown error')}")

    except Exception as e:
        traceback.print_exc()
        raise RuntimeError(f"Failed to get GIMP info: {e}")


# ---------------------------------------------------------------------------
# 🎨 Outil : get_context_state
# ---------------------------------------------------------------------------
@mcp.tool()
def get_context_state(ctx: Context) -> dict:
    """
    Récupère l'état du contexte GIMP (couleur FG/BG, brosse, mode, etc.).
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("get_context_state", params={})

        if result.get("status") == "success":
            return result["results"]
        else:
            raise RuntimeError(f"GIMP error: {result.get('error', 'Unknown error')}")

    except Exception as e:
        traceback.print_exc()
        raise RuntimeError(f"Failed to get context state: {e}")


# ---------------------------------------------------------------------------
# 🧠 Outil : call_api (exécution de code Python côté GIMP)
# ---------------------------------------------------------------------------
@mcp.tool()
def call_api(
    ctx: Context,
    api_path: str,
    args: list[list[str] | str] = [],
    kwargs: dict = {},
) -> dict:
    """
    Appelle l'API GIMP via le plugin (pyGObject-console, etc.).

    api_path = "exec" la plupart du temps,
    args = ["pyGObject-console", ["ligne1", "ligne2", ...]]
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "call_api",
            {
                "api_path": api_path,
                "args": args,
                "kwargs": kwargs,
            },
        )

        if result.get("status") == "success":
            return result["results"]
        else:
            # On renvoie aussi l’erreur au client MCP
            return {"error": result.get("error", "Unknown error")}

    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# 🎛️ Outil : apply_filter
# ---------------------------------------------------------------------------
@mcp.tool()
def apply_filter(
    ctx: Context,
    filter_type: str,
    params: dict | None = None,
) -> dict:
    """
    Applique un filtre GIMP (posterize, gaussian_blur, etc.) via le plugin.

    filter_type : string (posterize, gaussian_blur, oilify, edge_detect, desaturate)
    params      : dict de paramètres spécifiques au filtre
    """
    try:
        print(f"Requesting filter '{filter_type}' with params {params}...")

        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_filter",
            {
                "filter_type": filter_type,
                "params": params or {},
            },
        )

        if result.get("status") == "success":
            return result["results"]
        else:
            raise RuntimeError(f"GIMP error: {result.get('error', 'Unknown error')}")

    except Exception as e:
        traceback.print_exc()
        raise RuntimeError(f"Failed to apply filter: {e}")


# ---------------------------------------------------------------------------
# 📚 Prompts (best practices / workflow)
# ---------------------------------------------------------------------------
@mcp.prompt(
    description="GIMP MCP best practices for common operations - filling shapes, bezier paths, and variable persistence"
)
def gimp_best_practices() -> str:
    docs_path = Path(__file__).parent / "docs" / "best_practices.md"
    return docs_path.read_text(encoding="utf-8")


@mcp.prompt(
    description="Iterative workflow guidance for building complex images with proper validation and layer management"
)
def gimp_iterative_workflow() -> str:
    docs_path = Path(__file__).parent / "docs" / "iterative_workflow.md"
    return docs_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 🏁 Entrée principale : MCP via stdio
# ---------------------------------------------------------------------------
def main():
    # Transport MCP officiel côté Python : stdio (command mode)
    # Le client (Inspector, Claude, ou ton client Python) se connecte à ce script
    # en tant que "commande" MCP.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
