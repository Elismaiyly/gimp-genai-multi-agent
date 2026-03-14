#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client MCP GIMP amélioré avec filtres prédéfinis

Pipeline :
    client_mcp_test.py  --(MCP stdio)-->  gimp_mcp_server.py
                          (FastMCP)      |
                                         +--(TCP JSON)--> plugin GIMP (port 9877)

Fonctionnalités :
- Filtres artistiques prédéfinis (flou, dessin, couleurs)
- Nouveaux filtres apply_filter (posterize, gaussian_blur, etc.)
- Workflow automatique filtre + capture
- Interface utilisateur texte (menu)
- Gestion robuste des erreurs
"""

import asyncio
import json
import base64
import pathlib
import inspect
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client


# ============================================================
# 🎨 FILTRES PRÉDÉFINIS (call_api)
# ============================================================

FILTRES_PREDEFINIS = {
    "dessiner_ligne_rouge": {
        "name": "📏 Ligne rouge",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "gi.require_version('Gegl', '0.4')",
            "from gi.repository import Gimp, Gegl",
            "image = Gimp.get_images()[0]",
            "layer = image.get_layers()[0]",
            "Gimp.context_set_foreground(Gegl.Color.new('red'))",
            "Gimp.pencil(layer, [50, 50, 300, 300])",
            "Gimp.displays_flush()",
        ],
    },
    "dessiner_rectangle_bleu": {
        "name": "🟦 Rectangle bleu",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "gi.require_version('Gegl', '0.4')",
            "from gi.repository import Gimp, Gegl",
            "image = Gimp.get_images()[0]",
            "layer = image.get_layers()[0]",
            "Gimp.context_set_foreground(Gegl.Color.new('blue'))",
            "Gimp.pencil(layer, [100, 100, 400, 400])",
            "Gimp.displays_flush()",
        ],
    },
    "fond_vert": {
        "name": "🟢 Fond vert",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "gi.require_version('Gegl', '0.4')",
            "from gi.repository import Gimp, Gegl",
            "image = Gimp.get_images()[0]",
            "layer = image.get_layers()[0]",
            "Gimp.context_set_background(Gegl.Color.new('green'))",
            "Gimp.Drawable.edit_fill(layer, Gimp.FillType.BACKGROUND)",
            "Gimp.displays_flush()",
        ],
    },
    "dessiner_cercle_jaune": {
        "name": "🟡 Cercle jaune",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "gi.require_version('Gegl', '0.4')",
            "from gi.repository import Gimp, Gegl",
            "image = Gimp.get_images()[0]",
            "layer = image.get_layers()[0]",
            "Gimp.context_set_foreground(Gegl.Color.new('yellow'))",
            "image.select_ellipse(Gimp.ChannelOps.REPLACE, 150, 150, 200, 200)",
            "Gimp.Drawable.edit_fill(layer, Gimp.FillType.FOREGROUND)",
            "Gimp.Selection.none(image)",
            "Gimp.displays_flush()",
        ],
    },
    "texte_bonjour": {
        "name": "📝 Texte 'Bonjour'",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "gi.require_version('Gegl', '0.4')",
            "from gi.repository import Gimp, Gegl, GObject",
            "image = Gimp.get_images()[0]",
            "Gimp.context_set_foreground(Gegl.Color.new('purple'))",
            "text_layer = Gimp.text_layer_new(image, 'Bonjour !', 'Sans', 30, Gimp.Unit.PIXEL)",
            "image.insert_layer(text_layer, None, 0)",
            "text_layer.set_offsets(100, 100)",
            "Gimp.displays_flush()",
        ],
    },
    "flou_gaussien": {
        "name": "🌫️ Flou gaussien",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "from gi.repository import Gimp",
            "image = Gimp.get_images()[0]",
            "layer = image.get_layers()[0]",
            "Gimp.get_pdb().run_procedure('plug-in-gauss', [image, layer, 15.0, 15.0, 0])",
            "Gimp.displays_flush()",
        ],
    },
    "niveau_couleurs": {
        "name": "🎛️ Ajustement niveaux",
        "commands": [
            "import gi",
            "gi.require_version('Gimp', '3.0')",
            "from gi.repository import Gimp",
            "image = Gimp.get_images()[0]",
            "layer = image.get_layers()[0]",
            "Gimp.get_pdb().run_procedure('gimp-levels', [layer, 0, 50, 200, 0.0, 1.0, 1])",
            "Gimp.displays_flush()",
        ],
    },
}

# ============================================================
# 🧪 NOUVEAUX FILTRES APPLY_FILTER
# ============================================================

FILTRES_APPLY_FILTER = {
    "posterize": {
        "name": "🎨 Posterisation",
        "params": {"levels": 4},
        "description": "Réduit le nombre de couleurs (effet d'affiche)",
    },
    "gaussian_blur": {
        "name": "🌫️ Flou gaussien",
        "params": {"radius": 5.0},
        "description": "Applique un flou gaussien",
    },
    "oilify": {
        "name": "🖼️ Effet peinture à l'huile",
        "params": {"mask_size": 7},
        "description": "Effet de peinture à l'huile",
    },
    "edge_detect": {
        "name": "🔍 Détection de contours",
        "params": {"algorithm": 0, "amount": 2.0},
        "description": "Met en évidence les contours",
    },
    "desaturate": {
        "name": "⚫ Noir et blanc",
        "params": {"mode": 0},
        "description": "Convertit en niveaux de gris",
    },
}


# ============================================================
# 🔧 HELPERS DE CONVERSION
# ============================================================

def to_plain(obj: Any):
    """Convertit un objet pydantic/complexe en dict/list Python."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]

    try:
        if hasattr(obj, "model_dump"):
            return to_plain(obj.model_dump())
    except Exception:
        pass

    try:
        if hasattr(obj, "dict") and callable(obj.dict):
            return to_plain(obj.dict())
    except Exception:
        pass

    if hasattr(obj, "__dict__"):
        d = {}
        for k in dir(obj):
            if k.startswith("_"):
                continue
            try:
                v = getattr(obj, k)
            except Exception:
                continue
            if inspect.ismethod(v) or inspect.isfunction(v) or callable(v):
                continue
            d[k] = to_plain(v)
        return d

    try:
        return str(obj)
    except Exception:
        return None


def find_first_dict(obj: Any):
    """Trouve le premier dict dans une structure complexe."""
    p = to_plain(obj)
    if isinstance(p, dict):
        return p
    if isinstance(p, list):
        for v in p:
            d = find_first_dict(v)
            if d:
                return d
    return None


def find_base64_png(obj: Any):
    """Retourne la chaîne Base64 PNG si trouvée."""
    p = to_plain(obj)

    if isinstance(p, str):
        s = p.strip()
        if len(s) > 50 and s.startswith("iVBOR"):
            return s
        return None

    if isinstance(p, dict):
        for key in ("image_data", "data", "content", "blob", "results", "image"):
            if key in p:
                found = find_base64_png(p[key])
                if found:
                    return found
        for v in p.values():
            found = find_base64_png(v)
            if found:
                return found

    if isinstance(p, list):
        for v in p:
            found = find_base64_png(v)
            if found:
                return found

    return None


def extract_json_from_calltool(response) -> dict | None:
    """Essaye d'extraire un dict JSON utile depuis CallToolResult."""
    p = to_plain(response)

    if isinstance(p, dict) and "content" in p:
        for entry in p["content"]:
            if isinstance(entry, dict):
                for key in ("data", "json", "value", "results", "result"):
                    if key in entry and isinstance(entry[key], dict):
                        return entry[key]

                if "text" in entry and isinstance(entry["text"], str):
                    txt = entry["text"].strip()
                    if txt.startswith("{") and txt.endswith("}"):
                        try:
                            return json.loads(txt)
                        except Exception:
                            pass

            d = find_first_dict(entry)
            if d:
                return d

    return find_first_dict(p)


def save_image_from_response(response, filename: str | None = None):
    """Extrait une image PNG Base64 depuis la réponse MCP et la sauvegarde sur disque."""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gimp_result_{timestamp}.png"

    b64 = find_base64_png(response)
    if not b64:
        print("⚠️ Aucune image PNG trouvée dans la réponse MCP.")
        return None

    try:
        png_bytes = base64.b64decode(b64)
    except Exception as e:
        print("❌ Erreur Base64 :", e)
        return None

    out = pathlib.Path(filename)
    out.write_bytes(png_bytes)
    print("✅ Image sauvegardée :", out.resolve())
    return out


# ============================================================
# 🚀 FONCTIONS AVANCÉES
# ============================================================

async def appliquer_filtre_et_capture(session: ClientSession, filtre_id: str):
    """Applique un filtre prédéfini via call_api et capture le résultat."""
    if filtre_id not in FILTRES_PREDEFINIS:
        print(f"❌ Filtre '{filtre_id}' non trouvé")
        return None

    filtre = FILTRES_PREDEFINIS[filtre_id]
    print(f"🎨 Application du filtre : {filtre['name']}")

    try:
        response = await session.call_tool(
            "call_api",
            {
                "api_path": "exec",
                "args": ["pyGObject-console", filtre["commands"]],
            },
        )

        print("✅ Filtre appliqué avec succès")

        print("📸 Capture du résultat...")
        bitmap_response = await session.call_tool("get_image_bitmap", {})
        filename = f"filtre_{filtre_id}_{datetime.now().strftime('%H%M%S')}.png"
        saved_path = save_image_from_response(bitmap_response, filename)
        return saved_path

    except Exception as e:
        print(f"❌ Erreur lors de l'application du filtre: {e}")
        return None


async def appliquer_filtre_apply_filter(session: ClientSession, filtre_id: str):
    """Applique un filtre via apply_filter et capture le résultat."""
    if filtre_id not in FILTRES_APPLY_FILTER:
        print(f"❌ Filtre apply_filter '{filtre_id}' non trouvé")
        return None

    filtre = FILTRES_APPLY_FILTER[filtre_id]
    print(f"🎨 Application du filtre : {filtre['name']}")
    print(f"📝 Description : {filtre['description']}")

    try:
        response = await session.call_tool(
            "apply_filter",
            {
                "filter_type": filtre_id,
                "params": filtre["params"],
            },
        )

        extracted = extract_json_from_calltool(response)
        if extracted:
            print("\n✅ Résultat filtre :")
            print(json.dumps(extracted, indent=4, ensure_ascii=False))
        else:
            print("\n📨 Résultat brut :")
            print(json.dumps(to_plain(response), indent=4, ensure_ascii=False))

        print("\n📸 Capture de l'image après filtre...")
        bitmap_response = await session.call_tool("get_image_bitmap", {})
        filename = (
            f"apply_filter_{filtre_id}_{datetime.now().strftime('%H%M%S')}.png"
        )
        saved_path = save_image_from_response(bitmap_response, filename)
        return saved_path

    except Exception as e:
        print(f"❌ Erreur lors de l'application du filtre: {e}")
        return None


async def appliquer_filtre_personnalise(session: ClientSession, commands: List[str]):
    """Applique des commandes personnalisées et capture le résultat."""
    print("🎨 Application de commandes personnalisées...")

    try:
        response = await session.call_tool(
            "call_api",
            {
                "api_path": "exec",
                "args": ["pyGObject-console", commands],
            },
        )

        print("✅ Commandes exécutées avec succès")

        bitmap_response = await session.call_tool("get_image_bitmap", {})
        filename = f"personnalise_{datetime.now().strftime('%H%M%S')}.png"
        saved_path = save_image_from_response(bitmap_response, filename)
        return saved_path

    except Exception as e:
        print(f"❌ Erreur lors de l'exécution: {e}")
        return None


def afficher_menu_filtres():
    """Affiche le menu des filtres disponibles."""
    print("\n" + "=" * 60)
    print("🎨 FILTRES PRÉDÉFINIS (call_api)")
    print("=" * 60)

    for i, (filtre_id, filtre) in enumerate(FILTRES_PREDEFINIS.items(), 1):
        print(f"{i:2d}. {filtre_id:25} - {filtre['name']}")

    print("\n🔄 NOUVEAUX FILTRES (apply_filter)")
    print("=" * 60)

    for i, (filtre_id, filtre) in enumerate(FILTRES_APPLY_FILTER.items(), 1):
        print(
            f"{i + len(FILTRES_PREDEFINIS):2d}. {filtre_id:25} - {filtre['name']}"
        )
        print(f"     {filtre['description']}")

    print("\n🔧 OUTILS STANDARDS")
    print("=" * 60)
    print("metadata - Afficher les métadonnées de l'image")
    print("info    - Informations GIMP")
    print("context - État du contexte")
    print("capture - Capturer l'image actuelle")
    print("custom  - Commandes personnalisées")
    print("quitter - Quitter l'application")
    print("=" * 60)


# ============================================================
# 🚀 CLIENT PRINCIPAL
# ============================================================

async def main():
    # On lance le serveur MCP comme une commande locale via stdio
    server = SimpleNamespace(
        command="python3",
        args=["gimp_mcp_server.py"],
        cwd=".",        # répertoire contenant gimp_mcp_server.py
        env={},
        encoding="utf-8",
        encoding_error_handler="ignore",
    )

    print("🚀 Connexion au serveur GIMP MCP (FastMCP, stdio)...")

    try:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✅ Connecté au serveur MCP GimpMCP.\n")

                tools_result = await session.list_tools()
                tools_plain = to_plain(tools_result)
                tools_available: list[str] = []

                if isinstance(tools_plain, dict) and "tools" in tools_plain:
                    for t in tools_plain["tools"]:
                        tools_available.append(t.get("name", t))
                else:
                    tools_available = [str(t) for t in tools_plain]

                print("📋 Outils MCP disponibles :", tools_available)

                if "apply_filter" not in tools_available:
                    print(
                        "⚠️ ATTENTION: apply_filter n'est pas disponible dans les outils MCP"
                    )
                else:
                    print("✅ apply_filter est disponible !")

                # Boucle interactive
                while True:
                    afficher_menu_filtres()
                    print("\n➡️ Choisis une option :")
                    choix = input("👉 ").strip().lower()

                    if choix in ("quit", "exit", "quitter", "q"):
                        print("👋 Fin du client GIMP.")
                        break

                    if choix.isdigit():
                        index = int(choix) - 1
                        total_filtres = (
                            len(FILTRES_PREDEFINIS) + len(FILTRES_APPLY_FILTER)
                        )

                        if 0 <= index < len(FILTRES_PREDEFINIS):
                            filtre_id = list(FILTRES_PREDEFINIS.keys())[index]
                            await appliquer_filtre_et_capture(session, filtre_id)
                        elif len(FILTRES_PREDEFINIS) <= index < total_filtres:
                            filtre_index = index - len(FILTRES_PREDEFINIS)
                            filtre_id = list(FILTRES_APPLY_FILTER.keys())[filtre_index]
                            await appliquer_filtre_apply_filter(session, filtre_id)
                        else:
                            print("❌ Numéro de filtre invalide")
                        continue

                    if choix in FILTRES_PREDEFINIS:
                        await appliquer_filtre_et_capture(session, choix)
                        continue

                    if choix in FILTRES_APPLY_FILTER:
                        await appliquer_filtre_apply_filter(session, choix)
                        continue

                    if choix == "metadata":
                        print("📊 Récupération des métadonnées...")
                        response = await session.call_tool("get_image_metadata", {})
                        extracted = extract_json_from_calltool(response)
                        if extracted:
                            print(json.dumps(extracted, indent=4, ensure_ascii=False))
                        continue

                    if choix == "info":
                        print("ℹ️ Récupération des infos GIMP...")
                        response = await session.call_tool("get_gimp_info", {})
                        extracted = extract_json_from_calltool(response)
                        if extracted:
                            print(json.dumps(extracted, indent=4, ensure_ascii=False))
                        continue

                    if choix == "context":
                        print("🎨 Récupération du contexte...")
                        response = await session.call_tool("get_context_state", {})
                        extracted = extract_json_from_calltool(response)
                        if extracted:
                            print(json.dumps(extracted, indent=4, ensure_ascii=False))
                        continue

                    if choix == "capture":
                        print("📸 Capture de l'image...")
                        response = await session.call_tool("get_image_bitmap", {})
                        save_image_from_response(response, "capture.png")
                        continue

                    if choix == "custom":
                        print("\n💻 Mode commandes personnalisées")
                        print(
                            "Exemple: Gimp.context_set_foreground(Gegl.Color.new('red'))"
                        )
                        print("Tape tes commandes (ligne vide pour terminer):")

                        commands: list[str] = []
                        while True:
                            line = input("cmd> ").strip()
                            if not line:
                                break
                            commands.append(line)

                        if commands:
                            await appliquer_filtre_personnalise(session, commands)
                        continue

                    if choix in tools_available:
                        print(f"⚙️ Exécution : {choix}")
                        response = await session.call_tool(choix, {})
                        if choix == "get_image_bitmap":
                            save_image_from_response(response, "preview.png")
                        else:
                            extracted = extract_json_from_calltool(response)
                            if extracted:
                                print("\n📊 Résultat JSON :")
                                print(
                                    json.dumps(
                                        extracted, indent=4, ensure_ascii=False
                                    )
                                )
                            else:
                                print("\n📨 Résultat brut :")
                                print(
                                    json.dumps(
                                        to_plain(response), indent=4, ensure_ascii=False
                                    )
                                )
                    else:
                        print(
                            "❌ Option non reconnue. Tape un numéro ou un nom de filtre."
                        )

    except Exception as e:
        print("❌ Erreur inattendue dans le client MCP :", e)


if __name__ == "__main__":
    asyncio.run(main())
