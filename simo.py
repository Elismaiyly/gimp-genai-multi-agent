#!/usr/bin/env python3
# -*- coding: utf-8 -*-
print("### VERSION VSCode LOADED ###")
"""
GIMP MCP Plugin - Model Context Protocol integration for GIMPsml
Provides bitmap extraction and metadata access functionality
"""

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gegl


from gi.repository import Gimp
from gi.repository import GLib


import io
import sys
import json
import socket
import traceback
import threading
import base64
import tempfile
import os
import platform
import signal

# Constants for configuration and thresholds
LARGE_SCALING_THRESHOLD = 4.0  # Warn if scaling ratio exceeds this value
MAX_REGION_SIZE = 8192         # Maximum region dimension in pixels
DEFAULT_TIMEOUT_SECONDS = 30   # Default timeout for operations
from gi.repository import Gimp, Gio





def N_(message):
    return message


def _(message):
    return GLib.dgettext(None, message)


def exec_and_get_results(command, context):
    buffer = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer
    exec(command, context)
    sys.stdout = original_stdout
    output = buffer.getvalue()
    return output


class MCPPlugin(Gimp.PlugIn):
    def __init__(self, host='localhost', port=9877):
        super().__init__()
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

        # Contexte Python complet pour python-fu-exec
        self.context = {}
        try:
            import_statements = [
                "import gi",
                "gi.require_version('Gimp', '3.0')",
                "gi.require_version('Gegl', '0.4')",
                "gi.require_version('Gio', '2.0')",
                "from gi.repository import Gimp, Gegl, Gio, GObject",
                "import sys",
                "import traceback",
                "import os"
            ]
            for stmt in import_statements:
                exec(stmt, self.context)
            print("✅ Contexte Python initialisé avec succès")
        except Exception as e:
            print(f"❌ Erreur initialisation contexte: {e}")
            print(f"Traceback: {traceback.format_exc()}")

        self.auto_disconnect_client = True

    # ---------------------------------------------------------------------
    # Enregistrement plugin dans le menu GIMP
    # ---------------------------------------------------------------------
    def do_query_procedures(self):
        """Register the plugin procedure."""
        return ["plug-in-mcp-server"]

    def do_create_procedure(self, name):
        """Define the procedure properties."""
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self.run, None
        )
        procedure.set_menu_label(_("Start MCP Server"))
        procedure.set_documentation(
            _("Starts an MCP server to control GIMP externally"),
            _("Starts an MCP server to control GIMP externally"),
            name
        )
        procedure.set_attribution("Your Name", "Your Name", "2023")
        procedure.add_menu_path('<Image>/Tools/')
        return procedure

    # ---------------------------------------------------------------------
    # Gestion du serveur TCP
    # ---------------------------------------------------------------------
    def shutdown_server(self, signum=None, frame=None):
        """Gracefully shutdown the server."""
        print(f"Shutdown signal received (signal: {signum}), closing MCP server...")
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        """Run the plugin and start the server."""
        if self.running:
            print("MCP Server is already running")
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

        self.running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self.shutdown_server)
        signal.signal(signal.SIGINT, self.shutdown_server)

        try:
            print("Creating socket...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.settimeout(1.0)  # Timeout to allow checking self.running periodically
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            print(f"GimpMCP server started on {self.host}:{self.port}")

            while self.running:
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                except socket.timeout:
                    # Timeout allows us to check self.running flag
                    continue
                except OSError:
                    # Socket was closed (likely during shutdown)
                    break

                # Handle client in a separate thread
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client,)
                )
                client_thread.daemon = True
                client_thread.start()

            # Clean shutdown
            print("MCP server shutting down...")
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
            print("MCP server stopped")
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

        except Exception as e:
            print(f"Error starting server: {str(e)}")
            self.running = False

            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None

            if self.server_thread:
                self.server_thread.join(timeout=1.0)
                self.server_thread = None

            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def _handle_client(self, client):
        """Handle connected client"""
        buffer = b''

        # Receive data in chunks to handle larger payloads
        while True:
            data = client.recv(4096)
            if not data:
                break
            buffer += data

            # Try to detect full JSON
            try:
                if isinstance(buffer, (bytes, bytearray)):
                    request = buffer.decode('utf-8')
                else:
                    request = str(buffer)

                if request.strip():
                    json.loads(request)  # will raise if incomplete
                    break
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        if not buffer:
            print("Client disconnected")
            return

        if isinstance(buffer, (bytes, bytearray)):
            request = buffer.decode('utf-8')
        else:
            request = str(buffer)

        response = self.execute_command(request)
        print(f"response type: {type(response)}")

        if isinstance(response, dict):
            response_str = json.dumps(response)
        else:
            response_str = str(response)

        # Send response in chunks
        response_bytes = response_str.encode('utf-8')
        bytes_sent = 0
        while bytes_sent < len(response_bytes):
            chunk = response_bytes[bytes_sent:bytes_sent + 8192]
            client.sendall(chunk)
            bytes_sent += len(chunk)

        if self.auto_disconnect_client:
            client.close()
        return

    # ---------------------------------------------------------------------
    # CŒUR : parse la requête JSON et route vers la bonne fonction
    # ---------------------------------------------------------------------
    def execute_command(self, request):
        """Execute commands in GIMP's main thread."""
        try:
            # Cas spécial : désactiver la déconnexion auto
            if request == "disable_auto_disconnect":
                self.auto_disconnect_client = False
                return {
                    "status": "success",
                    "results": "OK"
                }

            # Parse JSON
            j = json.loads(request)

            # ------------------------------------------------------
            # 1) COMMANDES HAUT NIVEAU (notre pipeline)
            # ------------------------------------------------------
            if "type" in j and j["type"] == "apply_filter":
                params = j.get("params", {})
                return self._apply_filter(params)

            if "type" in j and j["type"] == "execute_actions":
                actions = j.get("actions", [])
                return self._execute_actions(actions)

            # ------------------------------------------------------
            # 2) COMMANDES EXISTANTES
            # ------------------------------------------------------
            if "type" in j and j["type"] == "get_image_bitmap":
                params = j.get("params", {})
                return self._get_current_image_bitmap(params)

            if "type" in j and j["type"] == "get_image_metadata":
                return self._get_current_image_metadata()

            if "type" in j and j["type"] == "get_gimp_info":
                return self._get_gimp_info()

            if "type" in j and j["type"] == "get_context_state":
                return self._get_context_state()

            # ------------------------------------------------------
            # 3) STRUCTURE python-fu (pour compatibilité)
            # ------------------------------------------------------
            if "cmds" in j:
                a = ['python-fu-exec', j["cmds"]]

            elif "params" in j:
                p = j["params"]
                if "args" in p:
                    a = p["args"]
                else:
                    return {
                        "status": "error",
                        "error": "Structure de commande non reconnue"
                    }
            else:
                return {
                    "status": "error",
                    "error": "Structure JSON invalide"
                }

            # ------------------------------------------------------
            # 4) Exécution python-fu-eval / python-fu-exec
            # ------------------------------------------------------
            if len(a) == 0:
                return {
                    "status": "error",
                    "error": "No command arguments provided"
                }

            if a[0] == 'python-fu-eval':
                if len(a) > 1:
                    print(f"evaluating exprs: {a[1]}")
                    vals = [str(eval(e)) for e in a[1]]
                    results = {
                        "status": "success",
                        "results": vals
                    }
                else:
                    results = {
                        "status": "success",
                        "results": "[NULL]"
                    }
                print(f"expression result: {results}")
                return results

            # python-fu-exec
            outputs = ["OK"]
            if len(a) > 1:
                print(f"Executing commands: {a[1]}")
                outputs = [exec_and_get_results(c, self.context) for c in a[1]]
            else:
                print("no command to execute")

            result = {
                "status": "success",
                "results": outputs
            }
            print(f"Command result: {result}")
            return result

        except Exception as e:
            error_msg = f"Error executing command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return {
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            }

    # ---------------------------------------------------------------------
    # NOUVEAU : exécution d'une liste d'actions haut niveau (SML → GIMP)
    # ---------------------------------------------------------------------
    

    # ---------------------------------------------------------------------
    # FILTRES GEGL : utilisé par apply_filter et execute_actions
    # ---------------------------------------------------------------------
    
    def _apply_filter(self, params, image=None, drawable=None):
        from gi.repository import Gimp

        filter_name = params.get("filter") or params.get("filter_type")
        if not filter_name:
            raise ValueError(f"No filter specified: {params}")

        # 🔑 UTILISER le drawable fourni par _execute_actions
        if image is None or drawable is None:
            images = Gimp.get_images()
            if not images:
                raise RuntimeError("No image open in GIMP")
            image = images[0]

            drawables = image.get_selected_drawables()
            if not drawables:
                raise RuntimeError("No selected drawable")
            drawable = drawables[0]
        print(
            "APPLY ON DRAWABLE:",
            drawable.get_name(),
            "size=",
            drawable.get_width(),
            drawable.get_height(),
            "type=",
            type(drawable)
        )





        pdb = Gimp.get_pdb()

        def run(proc_name, cfg_dict):
            proc = pdb.lookup_procedure(proc_name)
            if not proc:
                raise RuntimeError(f"PDB procedure not found: {proc_name}")

            cfg = proc.create_config()
            for k, v in cfg_dict.items():
                if cfg.find_property(k):
                    cfg.set_property(k, v)

            proc.run(cfg)



        # 3) APPELS PDB NATIFS (VISIBLES)
        if filter_name == "gaussian_blur":
            from gi.repository import Gimp

            radius = float(params.get("radius", 5.0))

            # --- mapping radius -> facteur de réduction ---
            # radius 5  => factor 2 (léger)
            # radius 15 => factor 3
            # radius 40 => factor 5-6 (fort)
            factor = max(2, int(radius / 10) + 2)

            w = drawable.get_width()
            h = drawable.get_height()

            # sécurité
            if w < 4 or h < 4:
                return {"status": "success", "details": "image too small to blur"}

            # dimensions réduites
            w2 = max(1, int(w / factor))
            h2 = max(1, int(h / factor))

            # Certains types de drawables ne supportent pas scale() : on gère proprement
            try:
                # 0/1/2 selon versions; on tente une interpolation raisonnable
                interp = getattr(Gimp, "InterpolationType", None)
                if interp and hasattr(interp, "CUBIC"):
                    i = interp.CUBIC
                else:
                    i = 2  # fallback

                # downscale puis upscale -> blur visible
                drawable.scale(w2, h2, i)
                drawable.scale(w, h, i)

            except Exception:
                # fallback via PDB si la méthode scale n'existe pas
                pdb = Gimp.get_pdb()

                def run(proc_name, cfg_dict):
                    proc = pdb.lookup_procedure(proc_name)
                    if not proc:
                        raise RuntimeError(f"PDB procedure not found: {proc_name}")
                    cfg = proc.create_config()
                    for k, v in cfg_dict.items():
                        if cfg.find_property(k):
                            cfg.set_property(k, v)
                    proc.run(cfg)

                # Procédure de scale (le nom peut varier selon build; on essaie les plus probables)
                for proc_name in ("gimp-layer-scale", "gimp-drawable-scale"):
                    try:
                        run(proc_name, {
                            "drawable": drawable,
                            "new-width": w2,
                            "new-height": h2,
                        })
                        run(proc_name, {
                            "drawable": drawable,
                            "new-width": w,
                            "new-height": h,
                        })
                        break
                    except Exception:
                        continue

            # refresh visuel
            drawable.update(0, 0, w, h)
            Gimp.displays_flush()



        elif filter_name == "desaturate":
            run("gimp-drawable-desaturate", {
                "drawable": drawable,
                "mode": 1,   # luminance
            })
            


        elif filter_name == "posterize":
            levels = int(params.get("levels", 4))
            run("plug-in-posterize", {
                "run-mode": Gimp.RunMode.NONINTERACTIVE,
                "image": image,
                "drawable": drawable,
                "levels": levels,
            })

        else:
            raise ValueError(f"Unsupported filter: {filter_name}")

        # 4) RAFRAÎCHISSEMENT VISUEL
        Gimp.displays_flush()

        return {
            "status": "success",
            "details": f"{filter_name} applied via PDB"
        }

    # ---------------------------------------------------------------------
    # get_image_bitmap : export de l'image courante en base64
    # ---------------------------------------------------------------------
    def _get_current_image_bitmap(self, params=None):
        """Get the current image as a base64-encoded bitmap with optional scaling and region selection."""
        import base64, tempfile, os
        from gi.repository import Gimp

        params = params or {}
        print(f"Getting current image bitmap with params: {params}")

        max_width = params.get("max_width")
        max_height = params.get("max_height")
        region = params.get("region", {})

        origin_x = region.get("origin_x")
        origin_y = region.get("origin_y")
        region_width = region.get("width")
        region_height = region.get("height")
        scaled_to_width = region.get("max_width")
        scaled_to_height = region.get("max_height")

        images = Gimp.get_images()
        if not images:
            return {"status": "error", "error": "No images open in GIMP"}

        original_image = images[0]
        orig_w, orig_h = original_image.get_width(), original_image.get_height()

        working_image = original_image
        should_delete_working = False

        # --------------------------------------------------
        # 1) REGION EXTRACTION
        # --------------------------------------------------
        if all(v is not None for v in (origin_x, origin_y, region_width, region_height)):
            if (
                origin_x < 0 or origin_y < 0 or
                origin_x + region_width > orig_w or
                origin_y + region_height > orig_h
            ):
                return {"status": "error", "error": "Invalid region bounds"}

            working_image = Gimp.Image.new(
                region_width, region_height, original_image.get_base_type()
            )
            should_delete_working = True

            original_image.select_rectangle(
                Gimp.ChannelOps.REPLACE,
                origin_x, origin_y, region_width, region_height
            )

            src_layer = original_image.get_layers()[0]
            new_layer = Gimp.Layer.new(
                working_image,
                "Region",
                region_width,
                region_height,
                src_layer.get_type(),
                100,
                Gimp.LayerMode.NORMAL
            )
            working_image.insert_layer(new_layer, None, 0)

            Gimp.edit_copy([src_layer])
            fs = Gimp.edit_paste(new_layer, True)[0]
            Gimp.floating_sel_anchor(fs)
            Gimp.Selection.none(original_image)

        # --------------------------------------------------
        # 2) SCALING
        # --------------------------------------------------
        final_image = working_image
        should_delete_final = should_delete_working

        max_w = scaled_to_width or max_width
        max_h = scaled_to_height or max_height

        if max_w and max_h:
            w, h = working_image.get_width(), working_image.get_height()
            ar = w / h

            if ar > (max_w / max_h):
                target_w = max_w
                target_h = int(max_w / ar)
            else:
                target_h = max_h
                target_w = int(max_h * ar)

            if target_w != w or target_h != h:
                final_image = working_image.duplicate()
                should_delete_final = True
                final_image.scale(target_w, target_h)

        # --------------------------------------------------
        # 3) EXPORT PNG (ROBUSTE GIMP 3)
        # --------------------------------------------------
        drawable = final_image.get_layers()[0]
        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        try:
            # 🔑 EXPORT UNIQUE ET SÛR
            self._safe_png_export(final_image, drawable, temp_path)

            with open(temp_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")

            return {
                "status": "success",
                "results": {
                    "image_data": encoded,
                    "format": "png",
                    "width": final_image.get_width(),
                    "height": final_image.get_height(),
                    "original_width": orig_w,
                    "original_height": orig_h,
                    "encoding": "base64",
                }
            }

        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                if should_delete_final and final_image != working_image:
                    final_image.delete()
                if should_delete_working and working_image != original_image:
                    working_image.delete()
            except Exception:
                pass


    def _precision_to_string(self, precision):
        """Convert GIMP precision enum to readable string."""
        try:
            precision_map = {
                100: "u8",        # Gimp.Precision.U8_LINEAR
                150: "u8-gamma",  # Gimp.Precision.U8_GAMMA
                200: "u16",       # Gimp.Precision.U16_LINEAR
                250: "u16-gamma", # Gimp.Precision.U16_GAMMA
                300: "u32",       # Gimp.Precision.U32_LINEAR
                350: "u32-gamma", # Gimp.Precision.U32_GAMMA
                500: "half",      # Gimp.Precision.HALF_LINEAR
                550: "half-gamma",
                600: "float",
                650: "float-gamma",
                700: "double",
                750: "double-gamma"
            }
            return precision_map.get(int(precision), f"precision-{precision}")
        except Exception:
            return str(precision)

    def _get_layer_type_string(self, layer):
        """Get layer type string with compatibility for different GIMP versions."""
        try:
            if hasattr(layer, 'get_type'):
                return str(layer.get_type())
            elif hasattr(layer, 'get_image_type'):
                return str(layer.get_image_type())
            elif hasattr(layer, 'type'):
                return str(layer.type)
            else:
                if layer.has_alpha():
                    return "RGBA"
                else:
                    return "RGB"
        except Exception as e:
            print(f"Warning: Could not determine layer type: {e}")
            return "unknown"

    def _get_gimp_info(self):
        """Get comprehensive information about GIMP installation and environment."""
        try:
            print("Getting GIMP environment information...")

            gimp_info = {}

            try:
                version_info = {}

                try:
                    if hasattr(Gimp, 'version'):
                        version_info["version_method"] = str(Gimp.version())
                except Exception as v_error:
                    version_info["version_method_error"] = str(v_error)

                for attr in ['MAJOR_VERSION', 'MINOR_VERSION', 'MICRO_VERSION']:
                    try:
                        if hasattr(Gimp, attr):
                            version_info[attr.lower()] = getattr(Gimp, attr)
                    except Exception as attr_error:
                        version_info[f"{attr.lower()}_error"] = str(attr_error)

                version_attrs = [attr for attr in dir(Gimp) if 'version' in attr.lower()]
                if version_attrs:
                    version_info["available_version_attributes"] = version_attrs

                version_string = "Unknown"
                try:
                    if hasattr(Gimp, 'VERSION'):
                        version_string = str(Gimp.VERSION)
                    elif hasattr(Gimp, 'version_string'):
                        version_string = str(Gimp.version_string())
                    elif hasattr(Gimp, 'get_version'):
                        version_string = str(Gimp.get_version())
                except Exception:
                    pass

                version_info["detected_version"] = version_string
                version_info["gimp_module_type"] = str(type(Gimp))

                gimp_info["version"] = version_info

            except Exception as version_error:
                print(f"Error getting version info: {version_error}")
                gimp_info["version"] = {"error": str(version_error)}

            try:
                directories = {}

                directory_methods = [
                    ('user_directory', 'directory'),
                    ('system_data_directory', 'data_directory'),
                    ('locale_directory', 'locale_directory'),
                    ('plugin_directory', 'plug_in_directory'),
                    ('sysconf_directory', 'sysconf_directory')
                ]

                for dir_name, method_name in directory_methods:
                    try:
                        if hasattr(Gimp, method_name):
                            method = getattr(Gimp, method_name)
                            if callable(method):
                                directories[dir_name] = str(method())
                            else:
                                directories[dir_name] = str(method)
                        else:
                            directories[f"{dir_name}_not_available"] = True
                    except Exception as method_error:
                        directories[f"{dir_name}_error"] = str(method_error)

                dir_attrs = [attr for attr in dir(Gimp) if 'dir' in attr.lower()]
                directories["available_directory_methods"] = dir_attrs

                gimp_info["directories"] = directories

            except Exception as dir_error:
                print(f"Error getting directory info: {dir_error}")
                gimp_info["directories"] = {"error": str(dir_error)}

            try:
                images = Gimp.get_images()
                gimp_info["session"] = {
                    "num_open_images": len(images),
                    "has_open_images": len(images) > 0,
                    "open_image_files": []
                }

                for i, image in enumerate(images):
                    try:
                        image_file = image.get_file()
                        file_info = {
                            "index": i,
                            "width": image.get_width(),
                            "height": image.get_height(),
                            "base_type": self._base_type_to_string(image.get_base_type()),
                            "is_dirty": image.is_dirty() if hasattr(image, 'is_dirty') else None
                        }

                        if image_file:
                            file_info.update({
                                "path": image_file.get_path() if hasattr(image_file, 'get_path') else None,
                                "basename": image_file.get_basename() if hasattr(image_file, 'get_basename') else None
                            })
                        else:
                            file_info["path"] = "Untitled"

                        gimp_info["session"]["open_image_files"].append(file_info)
                    except Exception as image_error:
                        print(f"Error getting image {i} info: {image_error}")
                        gimp_info["session"]["open_image_files"].append({
                            "index": i,
                            "error": str(image_error)
                        })

            except Exception as session_error:
                print(f"Error getting session info: {session_error}")
                gimp_info["session"] = {"error": str(session_error)}

            try:
                pdb = Gimp.get_pdb()
                pdb_info = {
                    "available": pdb is not None,
                    "type": str(type(pdb)) if pdb else None
                }

                if pdb:
                    sample_procedures = []
                    try:
                        test_procs = [
                            'file-png-export',
                            'gimp-file-save',
                            'gimp-image-new',
                            'python-fu-console'
                        ]
                        for proc_name in test_procs:
                            try:
                                proc = pdb.lookup_procedure(proc_name)
                                sample_procedures.append({
                                    "name": proc_name,
                                    "available": proc is not None,
                                    "type": str(type(proc)) if proc else None
                                })
                            except Exception:
                                sample_procedures.append({
                                    "name": proc_name,
                                    "available": False,
                                    "error": "lookup_failed"
                                })
                    except Exception as proc_error:
                        print(f"Error testing procedures: {proc_error}")

                    pdb_info["sample_procedures"] = sample_procedures

                gimp_info["pdb"] = pdb_info

            except Exception as pdb_error:
                print(f"Error getting PDB info: {pdb_error}")
                gimp_info["pdb"] = {"error": str(pdb_error)}

            try:
                capabilities = {
                    "has_python_console": True,
                    "mcp_server_running": True,
                    "supports_image_export": True,
                    "supports_metadata_export": True,
                    "supports_gimp_info": True,
                    "api_version": "3.0+",
                    "python_version": sys.version,
                    "available_modules": [],
                    "gimp_module_attributes": len(dir(Gimp)),
                    "gimp_methods": [
                        attr for attr in dir(Gimp)
                        if callable(getattr(Gimp, attr, None))
                    ][:20]
                }

                test_modules = [
                    'gi.repository.Gimp',
                    'gi.repository.Gegl',
                    'gi.repository.Gio',
                    'json',
                    'base64',
                    'tempfile'
                ]
                for module_name in test_modules:
                    try:
                        if module_name == 'gi.repository.Gimp':
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        elif module_name == 'gi.repository.Gegl':
                            from gi.repository import Gegl  # noqa: F401
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        elif module_name == 'gi.repository.Gio':
                            from gi.repository import Gio  # noqa: F401
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        else:
                            __import__(module_name)
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                    except ImportError:
                        capabilities["available_modules"].append(
                            {"name": module_name, "available": False}
                        )
                    except Exception as mod_error:
                        capabilities["available_modules"].append(
                            {"name": module_name, "available": False, "error": str(mod_error)}
                        )

                gimp_info["capabilities"] = capabilities

            except Exception as cap_error:
                print(f"Error getting capabilities: {cap_error}")
                gimp_info["capabilities"] = {"error": str(cap_error)}

            try:
                system_info = {
                    "platform": platform.platform(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_version": platform.python_version(),
                    "environment_vars": {
                        "HOME": os.environ.get("HOME"),
                        "USER": os.environ.get("USER"),
                        "GIMP_PLUG_IN_DIR": os.environ.get("GIMP_PLUG_IN_DIR"),
                        "GIMP_DATA_DIR": os.environ.get("GIMP_DATA_DIR")
                    }
                }

                gimp_info["system"] = system_info

            except Exception as sys_error:
                print(f"Error getting system info: {sys_error}")
                gimp_info["system"] = {"error": str(sys_error)}

            return {
                "status": "success",
                "results": gimp_info
            }

        except Exception as e:
            error_msg = f"Error getting GIMP info: {str(e)}\n{traceback.format_exc()}"
            return {
                "status": "error",
                "error": error_msg,
                "traceback": traceback.format_exc()
            }
    

    def _get_context_state(self):
        """Get current GIMP context state (colors, brush, tool settings)."""
        try:
            print("Getting GIMP context state...")

            context_state = {}

            try:
                fg_color = Gimp.context_get_foreground()
                bg_color = Gimp.context_get_background()

                context_state["foreground_color"] = {
                    "color_object": str(fg_color),
                    "description": "Current foreground color"
                }
                context_state["background_color"] = {
                    "color_object": str(bg_color),
                    "description": "Current background color"
                }

                try:
                    if hasattr(fg_color, 'get_rgba'):
                        rgba = fg_color.get_rgba()
                        context_state["foreground_color"]["rgba"] = list(rgba) if rgba else None
                except Exception as color_error:
                    context_state["foreground_color"]["rgba_error"] = str(color_error)

                try:
                    if hasattr(bg_color, 'get_rgba'):
                        rgba = bg_color.get_rgba()
                        context_state["background_color"]["rgba"] = list(rgba) if rgba else None
                except Exception as color_error:
                    context_state["background_color"]["rgba_error"] = str(color_error)

            except Exception as color_err:
                context_state["colors_error"] = str(color_err)

            try:
                brush = Gimp.context_get_brush()
                if brush:
                    context_state["brush"] = {
                        "name": brush.get_name() if hasattr(brush, 'get_name') else str(brush),
                        "description": "Current brush"
                    }
            except Exception as brush_err:
                context_state["brush_error"] = str(brush_err)

            try:
                opacity = Gimp.context_get_opacity()
                context_state["opacity"] = {
                    "value": opacity,
                    "description": "Current opacity percentage (0-100)"
                }
            except Exception as opacity_err:
                context_state["opacity_error"] = str(opacity_err)

            try:
                paint_mode = Gimp.context_get_paint_mode()
                context_state["paint_mode"] = {
                    "value": str(paint_mode),
                    "description": "Current paint/blend mode"
                }
            except Exception as mode_err:
                context_state["paint_mode_error"] = str(mode_err)

            try:
                feather = Gimp.context_get_feather()
                feather_radius = Gimp.context_get_feather_radius()
                context_state["feather"] = {
                    "enabled": feather,
                    "radius": feather_radius,
                    "description": "Selection feathering state"
                }
            except Exception as feather_err:
                context_state["feather_note"] = "Feather settings not available in context"

            try:
                antialias = Gimp.context_get_antialias()
                context_state["antialias"] = {
                    "enabled": antialias,
                    "description": "Antialiasing state for selections"
                }
            except Exception as aa_err:
                context_state["antialias_note"] = "Antialias setting not available"

            return {
                "status": "success",
                "results": context_state
            }

        except Exception as e:
            error_msg = f"Error getting context state: {str(e)}\n{traceback.format_exc()}"
            return {
                "status": "error",
                "error": error_msg,
                "traceback": traceback.format_exc()
    
          }
    
#25
    def _apply_gegl_to_drawable(self, drawable, op_name: str, props: dict):
        """
        Applique une opération GEGL de manière DESTRUCTIVE et fiable
        (buffer -> shadow -> merge_shadow).
        """
        from gi.repository import Gegl, Gimp

        src_buffer = drawable.get_buffer()
        shadow_buffer = drawable.get_shadow_buffer()

        graph = Gegl.Node()
        src = graph.create_child("gegl:buffer-source")
        src.set_property("buffer", src_buffer)

        op = graph.create_child(op_name)
        for k, v in (props or {}).items():
            op.set_property(k, v)

        sink = graph.create_child("gegl:buffer-sink")
        sink.set_property("buffer", shadow_buffer)

        src.link(op)
        op.link(sink)

        sink.process()

        drawable.merge_shadow(True)
        drawable.update(0, 0, drawable.get_width(), drawable.get_height())
        Gimp.displays_flush()
    def _brightness_contrast(self, drawable, brightness: float = 0.0, contrast: float = 0.0):
        """
        brightness et contrast attendus dans [-1.0, +1.0] (GEGL).
        """
        props = {
            "brightness": float(brightness),
            "contrast": float(contrast),
        }
        self._apply_gegl_to_drawable(drawable, "gegl:brightness-contrast", props)
        


    def _bw_grayscale(self, drawable):
        # noir et blanc
        self._apply_gegl_to_drawable(drawable, "gegl:gray", {})
#26
    def _debug_list_gauss_procs(self):
        from gi.repository import Gimp
        pdb = Gimp.get_pdb()

        # EXACTEMENT 8 paramètres explicites
        procs = pdb.query_procedures(
            "gauss",   # name
            "",        # blurb
            "",        # help
            "",        # author
            "",        # copyright
            "",        # date
            "",        # type
            ""         # path
        )

        print("\n=== GAUSS PROCEDURES ===")
        for p in procs:
            print(" -", p)
        print("=======================\n")

    #07
    

    def _safe_file_save(self, image, drawable, path):
        """
        Export PNG robuste compatible TOUS builds GIMP 3
        (drawable / drawables / aucun)
        """
        from gi.repository import Gimp, Gio

        file_obj = Gio.File.new_for_path(path)
        pdb = Gimp.get_pdb()

        export_proc = pdb.lookup_procedure("file-png-export")
        if not export_proc:
            raise RuntimeError("file-png-export not available")

        cfg = export_proc.create_config()

        # 🔑 Propriétés toujours valides
        cfg.set_property("image", image)
        cfg.set_property("file", file_obj)

        # 🔑 drawable OU drawables selon le build
        if cfg.find_property("drawable"):
            cfg.set_property("drawable", drawable)
        elif cfg.find_property("drawables"):
            cfg.set_property("drawables", [drawable])
        else:
            # OK : certains builds n’en veulent aucun
            pass

        # Options PNG sûres
        for k, v in {
            "compression": 9,
            "interlaced": False,
            "save-transparency": True,
            "save-color-profile": False,
            "save-exif": False,
            "save-xmp": False,
            "save-iptc": False,
        }.items():
            if cfg.find_property(k):
                cfg.set_property(k, v)

        print(f"[PNG EXPORT] {path}")
        export_proc.run(cfg)


     #08
    def _safe_png_export(self, image, drawable, path):
        from gi.repository import Gimp, Gio

        file_obj = Gio.File.new_for_path(path)
        pdb = Gimp.get_pdb()

        export_proc = pdb.lookup_procedure("file-png-export")
        if not export_proc:
            raise RuntimeError("file-png-export not available")

        cfg = export_proc.create_config()

        # Propriétés toujours valides
        cfg.set_property("image", image)
        cfg.set_property("file", file_obj)

        # 🔑 GIMP 3 : drawable OU drawables selon le build
        if cfg.find_property("drawable"):
            cfg.set_property("drawable", drawable)
        elif cfg.find_property("drawables"):
            cfg.set_property("drawables", [drawable])
        else:
            # certains builds n’en veulent aucun → OK
            pass

        # Options PNG sûres
        for k, v in {
            "compression": 9,
            "interlaced": False,
            "save-transparency": True,
            "save-color-profile": False,
            "save-exif": False,
            "save-xmp": False,
            "save-iptc": False,
        }.items():
            if cfg.find_property(k):
                cfg.set_property(k, v)

        export_proc.run(cfg)
    
    def _execute_actions(self, actions):
        """
        Reçoit une liste d'actions de haut niveau (Agent SML / Vision)
        et les exécute sur l'image courante dans GIMP 3.
        """
        import traceback
        import base64
        import tempfile
        import os
        from gi.repository import Gimp, Gio, GObject

        try:
            # -------------------------------------------------------
            # Vérifier qu'une image est ouverte
            # -------------------------------------------------------
            images = Gimp.get_images()
            if not images:
                return {"status": "error", "error": "No images open for execute_actions"}

            image = images[0]
            drawables = image.get_selected_drawables()
            if not drawables:
                return {"status": "error", "error": "No selected drawable in image"}

            drawable = drawables[0]


            executed = []

            # =======================================================
            # Boucle principale des actions
            # =======================================================
            for act in actions:
                name = act.get("action")
                params = act.get("params", {}) or {}

                # ===================================================
                # 1) apply_filter
                # ===================================================
                if name == "apply_filter":
                    filter_name = params.get("filter")

                    if filter_name == "gaussian_blur":
                        radius = float(params.get("radius", 5.0))
                        pf = {"filter": "gaussian_blur", "radius": radius}

                        self._apply_filter(pf, image=image, drawable=drawable)

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": f"gaussian_blur radius={radius}"
                        })
                        continue

                    elif filter_name == "posterize":
                        levels = int(params.get("levels", 4))
                        pf = {"filter": "posterize", "levels": levels}

                        self._apply_filter(pf, image=image, drawable=drawable)

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": f"posterize levels={levels}"
                        })
                        continue


                    elif filter_name in ("desaturate", "grayscale", "bw", "noir_et_blanc"):
                        pf = {"filter": "desaturate"}
                        self._apply_filter(pf, image=image, drawable=drawable)

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": "desaturate applied via PDB"
                        })
                        continue
                    elif filter_name in ("hue_saturation", "hue-saturation"):
                        from gi.repository import Gegl, Gimp

                        hue = float(params.get("hue", 0))            # -180..180 ou 0..360
                        saturation = float(params.get("saturation", 0))  # -100..100
                        lightness = float(params.get("lightness", 0))     # -100..100

                        # Normalisation GEGL
                        hue_norm = hue / 360.0
                        sat_norm = saturation / 100.0
                        light_norm = lightness / 100.0

                        src_buffer = drawable.get_buffer()
                        shadow_buffer = drawable.get_shadow_buffer()

                        graph = Gegl.Node()

                        src = graph.create_child("gegl:buffer-source")
                        src.set_property("buffer", src_buffer)

                        hs = graph.create_child("gegl:hue-saturation")
                        hs.set_property("hue", hue_norm)
                        hs.set_property("saturation", sat_norm)
                        hs.set_property("lightness", light_norm)

                        sink = graph.create_child("gegl:buffer-sink")
                        sink.set_property("buffer", shadow_buffer)

                        src.link(hs)
                        hs.link(sink)

                        sink.process()

                        drawable.merge_shadow(True)
                        drawable.update(0, 0, drawable.get_width(), drawable.get_height())
                        Gimp.displays_flush()

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": f"gegl:hue-saturation applied (h={hue}, s={saturation}, l={lightness})"
                        })
                        continue

               #

# ===================================================
# 6) 🎨 Colorisation (Couleurs → Colorier) — GIMP 3
# ===================================================
               
                elif name == "enhance":
                    # params: {"brightness": 0.2} ou {"contrast": 0.3}
                    b = params.get("brightness")
                    c = params.get("contrast")

                    if b is None and c is None:
                        executed.append({
                            "action": name,
                            "status": "skipped",
                            "reason": "enhance requires brightness or contrast"
                        })
                        continue

                    brightness = float(b) if b is not None else 0.0
                    contrast = float(c) if c is not None else 0.0

                    self._brightness_contrast(drawable, brightness=brightness, contrast=contrast)

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": f"brightness={brightness}, contrast={contrast}"
                    })
                    continue
    
                
  

                # ===================================================
                # 2) Sélection rectangle (fallback / debug)
                # ===================================================
                elif name == "select_rectangle":
                    x = int(params.get("x", 0))
                    y = int(params.get("y", 0))
                    w = int(params.get("width", 0))
                    h = int(params.get("height", 0))

                    Gimp.Image.select_rectangle(
                        image,
                        Gimp.ChannelOps.REPLACE,
                        x, y, w, h
                    )

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": f"rectangle x={x}, y={y}, w={w}, h={h}"
                    })
                    continue

                # ===================================================
                # 3) 🔥 Sélection par MASQUE PNG (SAM) — CORRIGÉ
                # ===================================================
                #janvier le 30
                elif name == "select_mask_png":
                    import base64, tempfile, os
                    from gi.repository import Gimp, Gio

                    png_b64 = params.get("png_b64")
                    offset_x = int(params.get("offset_x", 0))
                    offset_y = int(params.get("offset_y", 0))

                    if not png_b64 or len(png_b64) < 100:
                        raise RuntimeError("png_b64 missing or too small")

                    # -------------------------------------------------------
                    # 1) Base64 → PNG
                    # -------------------------------------------------------
                    png_data = base64.b64decode(png_b64)

                    fd, mask_path = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    with open(mask_path, "wb") as f:
                        f.write(png_data)

                    # -------------------------------------------------------
                    # 2) Charger le PNG comme calque
                    # -------------------------------------------------------
                    pdb = Gimp.get_pdb()
                    proc = pdb.lookup_procedure("gimp-file-load-layer")
                    cfg = proc.create_config()
                    cfg.set_property("image", image)
                    cfg.set_property("file", Gio.File.new_for_path(mask_path))

                    result = proc.run(cfg)
                    mask_layer = result.index(1)

                    image.insert_layer(mask_layer, None, 0)

                    # Sélection depuis l’alpha
                    Gimp.Image.select_item(
                        image,
                        Gimp.ChannelOps.REPLACE,
                        mask_layer
                    )

                    # 🔥 SUPPRESSION IMMÉDIATE DU CALQUE MASQUE
                    image.remove_layer(mask_layer)

                    Gimp.displays_flush()

                    if offset_x or offset_y:
                        Gimp.Selection.translate(image, offset_x, offset_y)

                    Gimp.displays_flush()

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": "mask selection applied (GIMP 3 FINAL STABLE)"
                    })
                    continue





                 
                                # ===================================================
                # 🎨 apply_colorize_on_selection (GIMP 3 - STABLE)
                # Recolore UNIQUEMENT la zone sélectionnée
                # ===================================================
                elif name == "apply_colorize_on_selection":
                    from gi.repository import Gimp

                    hue = float(params.get("hue", 0.0))              # 0..360
                    saturation = float(params.get("saturation", 50)) # -100..100 (ou 0..100 selon usage)
                    lightness = float(params.get("lightness", 0))    # -100..100

                    # (optionnel mais recommandé) vérifier qu'il y a une sélection non vide
                    try:
                        has_sel, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
                        if not has_sel or (x2 - x1) <= 1 or (y2 - y1) <= 1:
                            executed.append({
                                "action": name,
                                "status": "skipped",
                                "details": "no active selection"
                            })
                            continue
                    except Exception:
                        # si bounds n'est pas dispo, on tente quand même
                        pass

                    pdb = Gimp.get_pdb()

                    # Procédure la plus stable (Colorize HSL)
                    # NOTE: selon build, le nom peut varier légèrement, on essaie plusieurs.
                    proc_names = [
                        "gimp-drawable-colorize-hsl",
                        "gimp-drawable-colorize",       # fallback
                    ]

                    proc = None
                    for pn in proc_names:
                        try:
                            proc = pdb.lookup_procedure(pn)
                            if proc is not None:
                                proc_name_used = pn
                                break
                        except Exception:
                            proc = None

                    if proc is None:
                        raise RuntimeError("No PDB colorize procedure found (expected gimp-drawable-colorize-hsl).")

                    cfg = proc.create_config()

                    # Les propriétés peuvent varier selon la procédure -> on set seulement si dispo
                    # 1) drawable
                    for key in ("drawable", "layer", "item"):
                        try:
                            cfg.set_property(key, drawable)
                            break
                        except Exception:
                            pass

                    # 2) hue/sat/light
                    # gimp-drawable-colorize-hsl attend généralement H(0..360), S(-100..100), L(-100..100)
                    for (k, v) in (("hue", hue), ("saturation", saturation), ("lightness", lightness)):
                        try:
                            cfg.set_property(k, v)
                        except Exception:
                            # fallback noms alternatifs
                            if k == "saturation":
                                for alt in ("sat", "s"):
                                    try:
                                        cfg.set_property(alt, v)
                                        break
                                    except Exception:
                                        pass
                            if k == "lightness":
                                for alt in ("light", "l"):
                                    try:
                                        cfg.set_property(alt, v)
                                        break
                                    except Exception:
                                        pass

                    proc.run(cfg)
                    Gimp.displays_flush()

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": f"colorize applied on selection via {proc_name_used} (h={hue}, s={saturation}, l={lightness})"
                    })
                    continue












                                # ===================================================
                # 🔥 SMART INPAINT (GIMP ↔ LaMa)
                # ===================================================
# 🔥 SMART INPAINT (GIMP ↔ LaMa) — FIXED
# ===================================================
                elif name == "smart_inpaint":
                    import subprocess, tempfile, os
                    from gi.repository import Gimp, Gio, Gegl

                    try:
                        tmp_dir = tempfile.mkdtemp(prefix="gimp_lama_")
                        img_path  = os.path.join(tmp_dir, "image.png")
                        mask_path = os.path.join(tmp_dir, "mask.png")
                        out_path  = os.path.join(tmp_dir, "out.png")

                        print("[SMART_INPAINT] START")

                        # =================================================
                        # 1) EXPORT IMAGE RGB
                        # =================================================
                        self._safe_png_export(image, drawable, img_path)

                        # =================================================
                        # 2) MASQUE = CALQUE RGBA (NOIR / BLANC)
                        # =================================================
                        mask_layer = Gimp.Layer.new(
                            image,
                            "lama_mask",
                            image.get_width(),
                            image.get_height(),
                            Gimp.ImageType.RGBA_IMAGE,   # 🔑 PAS GRAY
                            100,
                            Gimp.LayerMode.NORMAL
                        )
                        image.insert_layer(mask_layer, None, 0)

                        # fond noir
                        Gimp.context_set_foreground(Gegl.Color.new("black"))
                        mask_layer.fill(Gimp.FillType.FOREGROUND)

                        # zone blanche = sélection
                        Gimp.context_set_foreground(Gegl.Color.new("white"))
                        mask_layer.edit_fill(Gimp.FillType.FOREGROUND)

                        # =================================================
                        # 3) EXPORT DU MASQUE
                        # =================================================
                        self._safe_png_export(image, mask_layer, mask_path)

                        image.remove_layer(mask_layer)

                        # =================================================
                        # 4) LANCER LaMa
                        # =================================================
                        subprocess.check_call([
                            "python3",
                            "/home/el-ismaiyly/gimp-mcp/pipeline/opencv_inpaint_final.py",
                            "--image", img_path,
                            "--mask", mask_path,
                            "--out", out_path
                        ])

                        if not os.path.exists(out_path):
                            raise RuntimeError("LaMa output missing")

                        # =================================================
                        # 5) IMPORT RÉSULTAT
                        # =================================================
                        new_layer = Gimp.file_load_layer(
                            Gimp.RunMode.NONINTERACTIVE,
                            image,
                            Gio.File.new_for_path(out_path)
                        )
                        image.insert_layer(new_layer, None, 0)

                        drawable.set_visible(False)
                        image.set_active_layer(new_layer)
                        Gimp.displays_flush()

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": "smart_inpaint OK"
                        })
                        continue

                    except Exception as e:
                        executed.append({
                            "action": name,
                            "status": "error",
                            "error": str(e)
                        })
                        continue


                    # ============================================================
                    # Helpers (compat GIMP 3 / fallback PDB)
                    # ============================================================
                    def _dup_layer(layer):
                        """Duplique un calque en essayant copy(), sinon PDB."""
                        try:
                            return layer.copy()
                        except Exception:
                            pdb = Gimp.get_pdb()
                            proc = pdb.lookup_procedure("gimp-layer-copy")
                            if not proc:
                                raise RuntimeError("Cannot duplicate layer: gimp-layer-copy missing")
                            cfg = proc.create_config()
                            cfg.set_property("layer", layer)
                            # certaines builds ont add-alpha / add_mask, on ignore si absent
                            if cfg.find_property("add-alpha"):
                                cfg.set_property("add-alpha", True)
                            res = proc.run(cfg)
                            try:
                                return res.index(1)
                            except Exception:
                                raise RuntimeError("Cannot duplicate layer (unknown return from gimp-layer-copy)")

                    def _add_mask_from_selection(layer):
                        """Ajoute un masque basé sur la sélection (API si dispo, sinon PDB)."""
                        try:
                            mask_obj = layer.create_mask(Gimp.AddMaskType.SELECTION)
                            layer.add_mask(mask_obj)
                            return
                        except Exception:
                            pdb = Gimp.get_pdb()
                            proc = pdb.lookup_procedure("gimp-layer-create-mask")
                            proc2 = pdb.lookup_procedure("gimp-layer-add-mask")
                            if (not proc) or (not proc2):
                                raise RuntimeError("Mask procedures unavailable (create/add mask)")
                            cfg = proc.create_config()
                            cfg.set_property("layer", layer)
                            cfg.set_property("add-mask-type", int(Gimp.AddMaskType.SELECTION))
                            out = proc.run(cfg)
                            try:
                                created_mask = out.index(1)
                            except Exception:
                                created_mask = None
                            if created_mask is None:
                                raise RuntimeError("Could not create mask from selection")
                            cfg2 = proc2.create_config()
                            cfg2.set_property("layer", layer)
                            cfg2.set_property("mask", created_mask)
                            proc2.run(cfg2)

                    def _select_from_mask_png(mask_png_path):
                        """Charge mask.png en calque temp, sélection = alpha, puis supprime le calque."""
                        tmp_mask_layer = Gimp.file_load_layer(
                            Gimp.RunMode.NONINTERACTIVE,
                            image,
                            Gio.File.new_for_path(mask_png_path)
                        )
                        image.insert_layer(tmp_mask_layer, None, 0)
                        # alpha -> sélection
                        Gimp.Image.select_item(image, Gimp.ChannelOps.REPLACE, tmp_mask_layer)
                        image.remove_layer(tmp_mask_layer)

                    def _safe_selection_none():
                        try:
                            Gimp.Selection.none(image)
                        except Exception:
                            pass

                    def _safe_selection_grow(px):
                        if px and px > 0:
                            try:
                                Gimp.Selection.grow(image, int(px))
                            except Exception:
                                pass

                    def _safe_selection_feather(px):
                        if px and px > 0:
                            try:
                                Gimp.Selection.feather(image, int(px))
                            except Exception:
                                pass

                    def _save_selection_to_channel(name_hint):
                        """Sauvegarde la sélection dans un channel temporaire et le renvoie."""
                        pdb = Gimp.get_pdb()
                        proc = pdb.lookup_procedure("gimp-selection-save")
                        if not proc:
                            return None
                        cfg = proc.create_config()
                        cfg.set_property("image", image)
                        out = proc.run(cfg)
                        ch = None
                        try:
                            ch = out.index(1)
                        except Exception:
                            ch = None
                        try:
                            if ch and hasattr(ch, "set_name"):
                                ch.set_name(name_hint)
                        except Exception:
                            pass
                        return ch

                    def _remove_channel(ch):
                        try:
                            pdb = Gimp.get_pdb()
                            proc = pdb.lookup_procedure("gimp-image-remove-channel")
                            if proc and ch:
                                cfg = proc.create_config()
                                cfg.set_property("image", image)
                                cfg.set_property("channel", ch)
                                proc.run(cfg)
                        except Exception:
                            pass

                    # =================================================
                    # START
                    # =================================================
                    try:
                        tmp_dir = tempfile.mkdtemp(prefix="gimp_lama_")
                        img_path  = os.path.join(tmp_dir, "image.png")
                        mask_path = os.path.join(tmp_dir, "mask.png")
                        out_path  = os.path.join(tmp_dir, "out.png")

                        print("[SMART_INPAINT] START (QUALITY MAX)")

                        # =================================================
                        # 0) Vérif sélection
                        # =================================================
                        if Gimp.Selection.is_empty(image):
                            raise RuntimeError("Selection is empty: nothing to inpaint")

                        # =================================================
                        # 1) EXPORT IMAGE RGB
                        # =================================================
                        self._safe_png_export(image, drawable, img_path)

                        # =================================================
                        # 2) MASQUE = CALQUE RGBA (NOIR / BLANC)
                        # =================================================
                        mask_layer = Gimp.Layer.new(
                            image,
                            "lama_mask",
                            image.get_width(),
                            image.get_height(),
                            Gimp.ImageType.RGBA_IMAGE,
                            100,
                            Gimp.LayerMode.NORMAL
                        )
                        image.insert_layer(mask_layer, None, 0)

                        # fond noir
                        Gimp.context_set_foreground(Gegl.Color.new("black"))
                        mask_layer.fill(Gimp.FillType.FOREGROUND)

                        # zone blanche = sélection active
                        Gimp.context_set_foreground(Gegl.Color.new("white"))
                        mask_layer.edit_fill(Gimp.FillType.FOREGROUND)

                        # =================================================
                        # 3) EXPORT DU MASQUE
                        # =================================================
                        self._safe_png_export(image, mask_layer, mask_path)
                        image.remove_layer(mask_layer)

                        # =================================================
                        # 4) LANCER LaMa
                        # =================================================
                        subprocess.check_call([
                            "python3",
                            "/home/el-ismaiyly/gimp-mcp/pipeline/opencv_inpaint_final.py",
                            "--image", img_path,
                            "--mask",  mask_path,
                            "--out",   out_path
                        ])

                        if not os.path.exists(out_path):
                            raise RuntimeError("LaMa output missing")

                        # =================================================
                        # 5) IMPORT RÉSULTAT (nouveau calque)
                        # =================================================
                        new_layer = Gimp.file_load_layer(
                            Gimp.RunMode.NONINTERACTIVE,
                            image,
                            Gio.File.new_for_path(out_path)
                        )
                        new_layer.set_name("lama_result")
                        image.insert_layer(new_layer, None, 0)

                        if HIDE_OLD:
                            try:
                                drawable.set_visible(False)
                            except Exception:
                                pass

                        # ============================================================
                        # 6) QUALITÉ MAX — PASS A : HARMONISATION LUMIÈRE (LOW FREQ)
                        # ============================================================
                        pdb = Gimp.get_pdb()

                        _safe_selection_none()
                        _select_from_mask_png(mask_path)
                        _safe_selection_grow(GROW_LARGE)
                        _safe_selection_feather(FEATHER_LARGE)

                        low_layer = _dup_layer(new_layer)
                        low_layer.set_name("blend_lowfreq")
                        image.insert_layer(low_layer, None, 0)

                        # Flou large via PDB (REMPLACE GEGL)
                        blur = pdb.lookup_procedure("plug-in-gauss")
                        if blur:
                            cfg = blur.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", low_layer)
                            cfg.set_property("horizontal", 18.0)
                            cfg.set_property("vertical", 18.0)
                            cfg.set_property("method", 0)
                            blur.run(cfg)

                        # Fusion lumière (ANTI-VOILE)
                        try:
                            low_layer.set_mode(Gimp.LayerMode.LCH_LIGHTNESS)
                            low_layer.set_opacity(55.0)   # ⬅ CRITIQUE (ne pas dépasser 60)
                        except Exception:
                            low_layer.set_mode(Gimp.LayerMode.SOFTLIGHT)
                            low_layer.set_opacity(22.0)

                        _add_mask_from_selection(low_layer)

                        # ============================================================
                        # 7) MICRO-CONTRASTE (UNSHARP MASK STABLE)
                        # ============================================================
                        _safe_selection_none()
                        _select_from_mask_png(mask_path)
                        _safe_selection_grow(GROW_FINE)
                        _safe_selection_feather(FEATHER_FINE)

                        hp_layer = _dup_layer(new_layer)
                        hp_layer.set_name("microcontrast")
                        image.insert_layer(hp_layer, None, 0)

                        sharp = pdb.lookup_procedure("plug-in-unsharp-mask")
                        if sharp:
                            cfg = sharp.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", hp_layer)
                            cfg.set_property("radius", 3.0)
                            cfg.set_property("amount", 0.6)
                            cfg.set_property("threshold", 0)
                            sharp.run(cfg)

                        try:
                            hp_layer.set_mode(Gimp.LayerMode.OVERLAY)
                            hp_layer.set_opacity(8.0)
                        except Exception:
                            pass

                        _add_mask_from_selection(hp_layer)

                        # ============================================================
                        # 8) GRAIN PHOTO DOUX (RGB NOISE)
                        # ============================================================
                        grain_layer = _dup_layer(new_layer)
                        grain_layer.set_name("photo_grain")
                        image.insert_layer(grain_layer, None, 0)

                        noise = pdb.lookup_procedure("plug-in-rgb-noise")
                        if noise:
                            cfg = noise.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", grain_layer)
                            cfg.set_property("correlated", True)
                            cfg.set_property("independent", False)
                            cfg.set_property("red", 0.03)
                            cfg.set_property("green", 0.03)
                            cfg.set_property("blue", 0.03)
                            cfg.set_property("alpha", 0.0)
                            noise.run(cfg)

                        # Flou léger du grain
                        if blur:
                            cfg = blur.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", grain_layer)
                            cfg.set_property("horizontal", 1.2)
                            cfg.set_property("vertical", 1.2)
                            cfg.set_property("method", 0)
                            blur.run(cfg)

                        try:
                            grain_layer.set_mode(Gimp.LayerMode.SOFTLIGHT)
                            grain_layer.set_opacity(10.0)
                        except Exception:
                            pass

                        _add_mask_from_selection(grain_layer)

                        # ============================================================
                        # 9) BONUS — TEXTURE BORROWING EN ANNEAU
                        # ============================================================
                        if ENABLE_RING_TEXTURE:
                            try:
                                _safe_selection_none()
                                _select_from_mask_png(mask_path)
                                _safe_selection_grow(RING_OUTER_GROW)
                                _safe_selection_feather(RING_FEATHER)
                                outer = _save_selection_to_channel("ring_outer")

                                _safe_selection_none()
                                _select_from_mask_png(mask_path)
                                _safe_selection_grow(RING_INNER_GROW)
                                _safe_selection_feather(int(RING_FEATHER * 0.6))
                                inner = _save_selection_to_channel("ring_inner")

                                Gimp.Image.select_item(image, Gimp.ChannelOps.REPLACE, outer)
                                Gimp.Image.select_item(image, Gimp.ChannelOps.SUBTRACT, inner)

                                tex_layer = _dup_layer(drawable)
                                tex_layer.set_name("ring_texture")
                                image.insert_layer(tex_layer, None, 0)

                                # Flou directionnel doux
                                if blur:
                                    cfg = blur.create_config()
                                    cfg.set_property("image", image)
                                    cfg.set_property("drawable", tex_layer)
                                    cfg.set_property("horizontal", 6.0)
                                    cfg.set_property("vertical", 2.0)
                                    cfg.set_property("method", 0)
                                    blur.run(cfg)

                                tex_layer.set_mode(Gimp.LayerMode.OVERLAY)
                                tex_layer.set_opacity(RING_OPACITY)

                                _add_mask_from_selection(tex_layer)

                                _remove_channel(outer)
                                _remove_channel(inner)

                            except Exception as e:
                                print("[QUALITY_MAX] Ring texture skipped:", e)

                        # Nettoyage sélection
                        _safe_selection_none()

                        # refresh
                        try:
                            new_layer.update(0, 0, new_layer.get_width(), new_layer.get_height())
                        except Exception:
                            pass
                        try:
                            Gimp.displays_flush()
                        except Exception:
                            pass

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": "smart_inpaint QUALITY MAX OK (PDB only)"
                        })
                        continue

                    except Exception as e:
                        executed.append({
                            "action": name,
                            "status": "error",
                            "error": str(e)
                        })
                        continue
                                              
                elif name == "clear_selection":
                    Gimp.Selection.none(image)
                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": "selection cleared"
                    })
                    continue


                # ===================================================
                # 6) Action inconnue
                # ===================================================
                else:
                    executed.append({
                        "action": name,
                        "status": "skipped",
                        "reason": f"Unknown or unsupported action '{name}'"
                    })
                    continue
                
            # ===================================================
            # 🧹 CLEANUP FINAL DES CALQUES TEMPORAIRES (PNG)
            # ===================================================
            for layer in list(image.get_layers()):
                try:
                    name = layer.get_name()
                    if name and name.startswith("tmp") and name.endswith(".png"):
                        image.remove_layer(layer)
                except Exception:
                    pass

            # -------------------------------------------------------
            # Rafraîchir l’affichage
            # -------------------------------------------------------
            Gimp.displays_flush()
            return {"status": "success", "results": {"executed": executed}}

        except Exception as e:
            tb = traceback.format_exc()
            print("Error in _execute_actions:", e)
            print(tb)
            return {"status": "error", "error": str(e), "traceback": tb}
        ()


    COLOR_NAMES = {
        "red": "#FF0000",
        "green": "#00FF00",
        "blue": "#0000FF",
        "white": "#FFFFFF",
        "black": "#000000",
        "yellow": "#FFFF00",
        "orange": "#FFA500",
        "purple": "#800080"
    }

    def normalize_color(self, col):
        if not col:
            return "#FF0000"
        col = col.lower().strip()
        if col.startswith("#"):
            return col
        if col in self.COLOR_NAMES:
            return self.COLOR_NAMES[col]
        return "#FF0000"

    def hex_to_rgb(self, h):
        h = h.lstrip("#")
        if len(h) != 6:
            raise ValueError("Invalid hex color")
        return (
            int(h[0:2], 16) / 255.0,
            int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0
        )

    def _draw_circle(self, image, drawable, params):
        """
        Dessine un cercle en utilisant une sélection elliptique + stroke.

        params attendus (tous optionnels sauf shape):
          - x : "center" ou coordonnée en pixels (float/int)
          - y : "center" ou coordonnée en pixels
          - radius : rayon en pixels (float/int)
          - stroke_width : épaisseur du trait (float, défaut = 5.0)
          - fill : "none" (par défaut) ou "fill" pour remplir le disque

        La couleur utilisée est la couleur de premier plan actuelle dans GIMP.
        """
        from gi.repository import Gimp

        # Taille de l'image
        width = image.get_width()
        height = image.get_height()

        # --- Centre du cercle ---
        x_param = params.get("x", "center")
        y_param = params.get("y", "center")

        if isinstance(x_param, (int, float)):
            cx = float(x_param)
        else:
            # "center" ou autre → on centre
            cx = width / 2.0

        if isinstance(y_param, (int, float)):
            cy = float(y_param)
        else:
            cy = height / 2.0

        # --- Rayon ---
        radius = float(params.get("radius", min(width, height) / 4.0))
        if radius <= 0:
            radius = min(width, height) / 4.0

        # BBox de l'ellipse
        left = int(cx - radius)
        top = int(cy - radius)
        w = int(2 * radius)
        h = int(2 * radius)

        # Clamp pour rester dans l'image
        if left < 0:
            left = 0
        if top < 0:
            top = 0
        if left + w > width:
            w = width - left
        if top + h > height:
            h = height - top

        # Option : épaisseur du trait
        stroke_width = float(params.get("stroke_width", 5.0))

        # Option : remplissage ou juste contour
        fill_mode = params.get("fill", "none")  # "none" ou "fill"

        # --- Dessin dans GIMP ---
        # On pousse le contexte pour ne pas casser les réglages de l'utilisateur
        Gimp.context_push()
        try:
            # Régler l'épaisseur du pinceau (si possible)
            try:
                Gimp.context_set_brush_size(stroke_width)
            except Exception as e:
                print("Warning: cannot set brush size:", e)

            # Créer une sélection elliptique
            image.select_ellipse(
                Gimp.ChannelOps.REPLACE,
                left,
                top,
                w,
                h
            )

            # Remplir si demandé
            if fill_mode == "fill":
                try:
                    Gimp.edit_fill(drawable, Gimp.FillType.FOREGROUND)
                except Exception as e:
                    print("Warning: edit_fill failed:", e)

            # Tracer le contour
            try:
                Gimp.edit_stroke(drawable)
            except Exception as e:
                print("Warning: edit_stroke failed:", e)

            # Optionnel : enlever la sélection
            try:
                image.select_none()
            except Exception as e:
                print("Warning: cannot clear selection:", e)

        finally:
            # Restaurer le contexte
            Gimp.context_pop()
            
    def _adjust_brightness(self, drawable, delta):
        """Ajuste la luminosité."""
        from gi.repository import Gimp
        Gimp.color_brightness_contrast(drawable, delta, 0)
    def _adjust_contrast(self, drawable, delta):
        """Ajuste le contraste."""
        from gi.repository import Gimp
        Gimp.color_brightness_contrast(drawable, 0, delta)
    def _desaturate(self, drawable):
        from gi.repository import Gimp
        gef = Gimp.DrawableFilter.new(drawable, "gegl:gray", "")
        gef.update()
        drawable.append_filter(gef)
        Gimp.displays_flush()




    


Gimp.main(MCPPlugin.__gtype__, sys.argv)











#""""""""""""""""""""""""""""""""""commancer l amelioration:
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
print("### VERSION VSCode LOADED ###")
"""
GIMP MCP Plugin - Model Context Protocol integration for GIMPsml
Provides bitmap extraction and metadata access functionality
"""

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gegl


from gi.repository import Gimp
from gi.repository import GLib


import io
import sys
import json
import socket
import traceback
import threading
import base64
import tempfile
import os
import platform
import signal

# Constants for configuration and thresholds
LARGE_SCALING_THRESHOLD = 4.0  # Warn if scaling ratio exceeds this value
MAX_REGION_SIZE = 8192         # Maximum region dimension in pixels
DEFAULT_TIMEOUT_SECONDS = 30   # Default timeout for operations
from gi.repository import Gimp, Gio





def N_(message):
    return message


def _(message):
    return GLib.dgettext(None, message)


def exec_and_get_results(command, context):
    buffer = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer
    exec(command, context)
    sys.stdout = original_stdout
    output = buffer.getvalue()
    return output


class MCPPlugin(Gimp.PlugIn):
    def __init__(self, host='localhost', port=9877):
        super().__init__()
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

        # Contexte Python complet pour python-fu-exec
        self.context = {}
        try:
            import_statements = [
                "import gi",
                "gi.require_version('Gimp', '3.0')",
                "gi.require_version('Gegl', '0.4')",
                "gi.require_version('Gio', '2.0')",
                "from gi.repository import Gimp, Gegl, Gio, GObject",
                "import sys",
                "import traceback",
                "import os"
            ]
            for stmt in import_statements:
                exec(stmt, self.context)
            print("✅ Contexte Python initialisé avec succès")
        except Exception as e:
            print(f"❌ Erreur initialisation contexte: {e}")
            print(f"Traceback: {traceback.format_exc()}")

        self.auto_disconnect_client = True

    # ---------------------------------------------------------------------
    # Enregistrement plugin dans le menu GIMP
    # ---------------------------------------------------------------------
    def do_query_procedures(self):
        """Register the plugin procedure."""
        return ["plug-in-mcp-server"]

    def do_create_procedure(self, name):
        """Define the procedure properties."""
        procedure = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self.run, None
        )
        procedure.set_menu_label(_("Start MCP Server"))
        procedure.set_documentation(
            _("Starts an MCP server to control GIMP externally"),
            _("Starts an MCP server to control GIMP externally"),
            name
        )
        procedure.set_attribution("Your Name", "Your Name", "2023")
        procedure.add_menu_path('<Image>/Tools/')
        return procedure

    # ---------------------------------------------------------------------
    # Gestion du serveur TCP
    # ---------------------------------------------------------------------
    def shutdown_server(self, signum=None, frame=None):
        """Gracefully shutdown the server."""
        print(f"Shutdown signal received (signal: {signum}), closing MCP server...")
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        """Run the plugin and start the server."""
        if self.running:
            print("MCP Server is already running")
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

        self.running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self.shutdown_server)
        signal.signal(signal.SIGINT, self.shutdown_server)

        try:
            print("Creating socket...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.settimeout(1.0)  # Timeout to allow checking self.running periodically
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            print(f"GimpMCP server started on {self.host}:{self.port}")

            while self.running:
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                except socket.timeout:
                    # Timeout allows us to check self.running flag
                    continue
                except OSError:
                    # Socket was closed (likely during shutdown)
                    break

                # Handle client in a separate thread
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client,)
                )
                client_thread.daemon = True
                client_thread.start()

            # Clean shutdown
            print("MCP server shutting down...")
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
            print("MCP server stopped")
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

        except Exception as e:
            print(f"Error starting server: {str(e)}")
            self.running = False

            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None

            if self.server_thread:
                self.server_thread.join(timeout=1.0)
                self.server_thread = None

            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def _handle_client(self, client):
        """Handle connected client"""
        buffer = b''

        # Receive data in chunks to handle larger payloads
        while True:
            data = client.recv(4096)
            if not data:
                break
            buffer += data

            # Try to detect full JSON
            try:
                if isinstance(buffer, (bytes, bytearray)):
                    request = buffer.decode('utf-8')
                else:
                    request = str(buffer)

                if request.strip():
                    json.loads(request)  # will raise if incomplete
                    break
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        if not buffer:
            print("Client disconnected")
            return

        if isinstance(buffer, (bytes, bytearray)):
            request = buffer.decode('utf-8')
        else:
            request = str(buffer)

        response = self.execute_command(request)
        print(f"response type: {type(response)}")

        if isinstance(response, dict):
            response_str = json.dumps(response)
        else:
            response_str = str(response)

        # Send response in chunks
        response_bytes = response_str.encode('utf-8')
        bytes_sent = 0
        while bytes_sent < len(response_bytes):
            chunk = response_bytes[bytes_sent:bytes_sent + 8192]
            client.sendall(chunk)
            bytes_sent += len(chunk)

        if self.auto_disconnect_client:
            client.close()
        return

    # ---------------------------------------------------------------------
    # CŒUR : parse la requête JSON et route vers la bonne fonction
    # ---------------------------------------------------------------------
    def execute_command(self, request):
        """Execute commands in GIMP's main thread."""
        try:
            # Cas spécial : désactiver la déconnexion auto
            if request == "disable_auto_disconnect":
                self.auto_disconnect_client = False
                return {
                    "status": "success",
                    "results": "OK"
                }

            # Parse JSON
            j = json.loads(request)

            # ------------------------------------------------------
            # 1) COMMANDES HAUT NIVEAU (notre pipeline)
            # ------------------------------------------------------
            if "type" in j and j["type"] == "apply_filter":
                params = j.get("params", {})
                return self._apply_filter(params)

            if "type" in j and j["type"] == "execute_actions":
                actions = j.get("actions", [])
                return self._execute_actions(actions)

            # ------------------------------------------------------
            # 2) COMMANDES EXISTANTES
            # ------------------------------------------------------
            if "type" in j and j["type"] == "get_image_bitmap":
                params = j.get("params", {})
                return self._get_current_image_bitmap(params)

            if "type" in j and j["type"] == "get_image_metadata":
                return self._get_current_image_metadata()

            if "type" in j and j["type"] == "get_gimp_info":
                return self._get_gimp_info()

            if "type" in j and j["type"] == "get_context_state":
                return self._get_context_state()

            # ------------------------------------------------------
            # 3) STRUCTURE python-fu (pour compatibilité)
            # ------------------------------------------------------
            if "cmds" in j:
                a = ['python-fu-exec', j["cmds"]]

            elif "params" in j:
                p = j["params"]
                if "args" in p:
                    a = p["args"]
                else:
                    return {
                        "status": "error",
                        "error": "Structure de commande non reconnue"
                    }
            else:
                return {
                    "status": "error",
                    "error": "Structure JSON invalide"
                }

            # ------------------------------------------------------
            # 4) Exécution python-fu-eval / python-fu-exec
            # ------------------------------------------------------
            if len(a) == 0:
                return {
                    "status": "error",
                    "error": "No command arguments provided"
                }

            if a[0] == 'python-fu-eval':
                if len(a) > 1:
                    print(f"evaluating exprs: {a[1]}")
                    vals = [str(eval(e)) for e in a[1]]
                    results = {
                        "status": "success",
                        "results": vals
                    }
                else:
                    results = {
                        "status": "success",
                        "results": "[NULL]"
                    }
                print(f"expression result: {results}")
                return results

            # python-fu-exec
            outputs = ["OK"]
            if len(a) > 1:
                print(f"Executing commands: {a[1]}")
                outputs = [exec_and_get_results(c, self.context) for c in a[1]]
            else:
                print("no command to execute")

            result = {
                "status": "success",
                "results": outputs
            }
            print(f"Command result: {result}")
            return result

        except Exception as e:
            error_msg = f"Error executing command: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return {
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            }

    # ---------------------------------------------------------------------
    # NOUVEAU : exécution d'une liste d'actions haut niveau (SML → GIMP)
    # ---------------------------------------------------------------------
    

    # ---------------------------------------------------------------------
    # FILTRES GEGL : utilisé par apply_filter et execute_actions
    # ---------------------------------------------------------------------
    
    def _apply_filter(self, params, image=None, drawable=None):
        from gi.repository import Gimp

        filter_name = params.get("filter") or params.get("filter_type")
        if not filter_name:
            raise ValueError(f"No filter specified: {params}")

        # 🔑 UTILISER le drawable fourni par _execute_actions
        if image is None or drawable is None:
            images = Gimp.get_images()
            if not images:
                raise RuntimeError("No image open in GIMP")
            image = images[0]

            drawables = image.get_selected_drawables()
            if not drawables:
                raise RuntimeError("No selected drawable")
            drawable = drawables[0]
        print(
            "APPLY ON DRAWABLE:",
            drawable.get_name(),
            "size=",
            drawable.get_width(),
            drawable.get_height(),
            "type=",
            type(drawable)
        )





        pdb = Gimp.get_pdb()

        def run(proc_name, cfg_dict):
            proc = pdb.lookup_procedure(proc_name)
            if not proc:
                raise RuntimeError(f"PDB procedure not found: {proc_name}")

            cfg = proc.create_config()
            for k, v in cfg_dict.items():
                if cfg.find_property(k):
                    cfg.set_property(k, v)

            proc.run(cfg)



        # 3) APPELS PDB NATIFS (VISIBLES)
        if filter_name == "gaussian_blur":
            from gi.repository import Gimp

            radius = float(params.get("radius", 5.0))

            # --- mapping radius -> facteur de réduction ---
            # radius 5  => factor 2 (léger)
            # radius 15 => factor 3
            # radius 40 => factor 5-6 (fort)
            factor = max(2, int(radius / 10) + 2)

            w = drawable.get_width()
            h = drawable.get_height()

            # sécurité
            if w < 4 or h < 4:
                return {"status": "success", "details": "image too small to blur"}

            # dimensions réduites
            w2 = max(1, int(w / factor))
            h2 = max(1, int(h / factor))

            # Certains types de drawables ne supportent pas scale() : on gère proprement
            try:
                # 0/1/2 selon versions; on tente une interpolation raisonnable
                interp = getattr(Gimp, "InterpolationType", None)
                if interp and hasattr(interp, "CUBIC"):
                    i = interp.CUBIC
                else:
                    i = 2  # fallback

                # downscale puis upscale -> blur visible
                drawable.scale(w2, h2, i)
                drawable.scale(w, h, i)

            except Exception:
                # fallback via PDB si la méthode scale n'existe pas
                pdb = Gimp.get_pdb()

                def run(proc_name, cfg_dict):
                    proc = pdb.lookup_procedure(proc_name)
                    if not proc:
                        raise RuntimeError(f"PDB procedure not found: {proc_name}")
                    cfg = proc.create_config()
                    for k, v in cfg_dict.items():
                        if cfg.find_property(k):
                            cfg.set_property(k, v)
                    proc.run(cfg)

                # Procédure de scale (le nom peut varier selon build; on essaie les plus probables)
                for proc_name in ("gimp-layer-scale", "gimp-drawable-scale"):
                    try:
                        run(proc_name, {
                            "drawable": drawable,
                            "new-width": w2,
                            "new-height": h2,
                        })
                        run(proc_name, {
                            "drawable": drawable,
                            "new-width": w,
                            "new-height": h,
                        })
                        break
                    except Exception:
                        continue

            # refresh visuel
            drawable.update(0, 0, w, h)
            Gimp.displays_flush()



        elif filter_name == "desaturate":
            run("gimp-drawable-desaturate", {
                "drawable": drawable,
                "mode": 1,   # luminance
            })
            


        elif filter_name == "posterize":
            levels = int(params.get("levels", 4))
            run("plug-in-posterize", {
                "run-mode": Gimp.RunMode.NONINTERACTIVE,
                "image": image,
                "drawable": drawable,
                "levels": levels,
            })

        else:
            raise ValueError(f"Unsupported filter: {filter_name}")

        # 4) RAFRAÎCHISSEMENT VISUEL
        Gimp.displays_flush()

        return {
            "status": "success",
            "details": f"{filter_name} applied via PDB"
        }

    # ---------------------------------------------------------------------
    # get_image_bitmap : export de l'image courante en base64
    # ---------------------------------------------------------------------
    def _get_current_image_bitmap(self, params=None):
        """Get the current image as a base64-encoded bitmap with optional scaling and region selection."""
        import base64, tempfile, os
        from gi.repository import Gimp

        params = params or {}
        print(f"Getting current image bitmap with params: {params}")

        max_width = params.get("max_width")
        max_height = params.get("max_height")
        region = params.get("region", {})

        origin_x = region.get("origin_x")
        origin_y = region.get("origin_y")
        region_width = region.get("width")
        region_height = region.get("height")
        scaled_to_width = region.get("max_width")
        scaled_to_height = region.get("max_height")

        images = Gimp.get_images()
        if not images:
            return {"status": "error", "error": "No images open in GIMP"}

        original_image = images[0]
        orig_w, orig_h = original_image.get_width(), original_image.get_height()

        working_image = original_image
        should_delete_working = False

        # --------------------------------------------------
        # 1) REGION EXTRACTION
        # --------------------------------------------------
        if all(v is not None for v in (origin_x, origin_y, region_width, region_height)):
            if (
                origin_x < 0 or origin_y < 0 or
                origin_x + region_width > orig_w or
                origin_y + region_height > orig_h
            ):
                return {"status": "error", "error": "Invalid region bounds"}

            working_image = Gimp.Image.new(
                region_width, region_height, original_image.get_base_type()
            )
            should_delete_working = True

            original_image.select_rectangle(
                Gimp.ChannelOps.REPLACE,
                origin_x, origin_y, region_width, region_height
            )

            src_layer = original_image.get_layers()[0]
            new_layer = Gimp.Layer.new(
                working_image,
                "Region",
                region_width,
                region_height,
                src_layer.get_type(),
                100,
                Gimp.LayerMode.NORMAL
            )
            working_image.insert_layer(new_layer, None, 0)

            Gimp.edit_copy([src_layer])
            fs = Gimp.edit_paste(new_layer, True)[0]
            Gimp.floating_sel_anchor(fs)
            Gimp.Selection.none(original_image)

        # --------------------------------------------------
        # 2) SCALING
        # --------------------------------------------------
        final_image = working_image
        should_delete_final = should_delete_working

        max_w = scaled_to_width or max_width
        max_h = scaled_to_height or max_height

        if max_w and max_h:
            w, h = working_image.get_width(), working_image.get_height()
            ar = w / h

            if ar > (max_w / max_h):
                target_w = max_w
                target_h = int(max_w / ar)
            else:
                target_h = max_h
                target_w = int(max_h * ar)

            if target_w != w or target_h != h:
                final_image = working_image.duplicate()
                should_delete_final = True
                final_image.scale(target_w, target_h)

        # --------------------------------------------------
        # 3) EXPORT PNG (ROBUSTE GIMP 3)
        # --------------------------------------------------
        drawable = final_image.get_layers()[0]
        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        try:
            # 🔑 EXPORT UNIQUE ET SÛR
            self._safe_png_export(final_image, drawable, temp_path)

            with open(temp_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")

            return {
                "status": "success",
                "results": {
                    "image_data": encoded,
                    "format": "png",
                    "width": final_image.get_width(),
                    "height": final_image.get_height(),
                    "original_width": orig_w,
                    "original_height": orig_h,
                    "encoding": "base64",
                }
            }

        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                if should_delete_final and final_image != working_image:
                    final_image.delete()
                if should_delete_working and working_image != original_image:
                    working_image.delete()
            except Exception:
                pass


    def _precision_to_string(self, precision):
        """Convert GIMP precision enum to readable string."""
        try:
            precision_map = {
                100: "u8",        # Gimp.Precision.U8_LINEAR
                150: "u8-gamma",  # Gimp.Precision.U8_GAMMA
                200: "u16",       # Gimp.Precision.U16_LINEAR
                250: "u16-gamma", # Gimp.Precision.U16_GAMMA
                300: "u32",       # Gimp.Precision.U32_LINEAR
                350: "u32-gamma", # Gimp.Precision.U32_GAMMA
                500: "half",      # Gimp.Precision.HALF_LINEAR
                550: "half-gamma",
                600: "float",
                650: "float-gamma",
                700: "double",
                750: "double-gamma"
            }
            return precision_map.get(int(precision), f"precision-{precision}")
        except Exception:
            return str(precision)

    def _get_layer_type_string(self, layer):
        """Get layer type string with compatibility for different GIMP versions."""
        try:
            if hasattr(layer, 'get_type'):
                return str(layer.get_type())
            elif hasattr(layer, 'get_image_type'):
                return str(layer.get_image_type())
            elif hasattr(layer, 'type'):
                return str(layer.type)
            else:
                if layer.has_alpha():
                    return "RGBA"
                else:
                    return "RGB"
        except Exception as e:
            print(f"Warning: Could not determine layer type: {e}")
            return "unknown"

    def _get_gimp_info(self):
        """Get comprehensive information about GIMP installation and environment."""
        try:
            print("Getting GIMP environment information...")

            gimp_info = {}

            try:
                version_info = {}

                try:
                    if hasattr(Gimp, 'version'):
                        version_info["version_method"] = str(Gimp.version())
                except Exception as v_error:
                    version_info["version_method_error"] = str(v_error)

                for attr in ['MAJOR_VERSION', 'MINOR_VERSION', 'MICRO_VERSION']:
                    try:
                        if hasattr(Gimp, attr):
                            version_info[attr.lower()] = getattr(Gimp, attr)
                    except Exception as attr_error:
                        version_info[f"{attr.lower()}_error"] = str(attr_error)

                version_attrs = [attr for attr in dir(Gimp) if 'version' in attr.lower()]
                if version_attrs:
                    version_info["available_version_attributes"] = version_attrs

                version_string = "Unknown"
                try:
                    if hasattr(Gimp, 'VERSION'):
                        version_string = str(Gimp.VERSION)
                    elif hasattr(Gimp, 'version_string'):
                        version_string = str(Gimp.version_string())
                    elif hasattr(Gimp, 'get_version'):
                        version_string = str(Gimp.get_version())
                except Exception:
                    pass

                version_info["detected_version"] = version_string
                version_info["gimp_module_type"] = str(type(Gimp))

                gimp_info["version"] = version_info

            except Exception as version_error:
                print(f"Error getting version info: {version_error}")
                gimp_info["version"] = {"error": str(version_error)}

            try:
                directories = {}

                directory_methods = [
                    ('user_directory', 'directory'),
                    ('system_data_directory', 'data_directory'),
                    ('locale_directory', 'locale_directory'),
                    ('plugin_directory', 'plug_in_directory'),
                    ('sysconf_directory', 'sysconf_directory')
                ]

                for dir_name, method_name in directory_methods:
                    try:
                        if hasattr(Gimp, method_name):
                            method = getattr(Gimp, method_name)
                            if callable(method):
                                directories[dir_name] = str(method())
                            else:
                                directories[dir_name] = str(method)
                        else:
                            directories[f"{dir_name}_not_available"] = True
                    except Exception as method_error:
                        directories[f"{dir_name}_error"] = str(method_error)

                dir_attrs = [attr for attr in dir(Gimp) if 'dir' in attr.lower()]
                directories["available_directory_methods"] = dir_attrs

                gimp_info["directories"] = directories

            except Exception as dir_error:
                print(f"Error getting directory info: {dir_error}")
                gimp_info["directories"] = {"error": str(dir_error)}

            try:
                images = Gimp.get_images()
                gimp_info["session"] = {
                    "num_open_images": len(images),
                    "has_open_images": len(images) > 0,
                    "open_image_files": []
                }

                for i, image in enumerate(images):
                    try:
                        image_file = image.get_file()
                        file_info = {
                            "index": i,
                            "width": image.get_width(),
                            "height": image.get_height(),
                            "base_type": self._base_type_to_string(image.get_base_type()),
                            "is_dirty": image.is_dirty() if hasattr(image, 'is_dirty') else None
                        }

                        if image_file:
                            file_info.update({
                                "path": image_file.get_path() if hasattr(image_file, 'get_path') else None,
                                "basename": image_file.get_basename() if hasattr(image_file, 'get_basename') else None
                            })
                        else:
                            file_info["path"] = "Untitled"

                        gimp_info["session"]["open_image_files"].append(file_info)
                    except Exception as image_error:
                        print(f"Error getting image {i} info: {image_error}")
                        gimp_info["session"]["open_image_files"].append({
                            "index": i,
                            "error": str(image_error)
                        })

            except Exception as session_error:
                print(f"Error getting session info: {session_error}")
                gimp_info["session"] = {"error": str(session_error)}

            try:
                pdb = Gimp.get_pdb()
                pdb_info = {
                    "available": pdb is not None,
                    "type": str(type(pdb)) if pdb else None
                }

                if pdb:
                    sample_procedures = []
                    try:
                        test_procs = [
                            'file-png-export',
                            'gimp-file-save',
                            'gimp-image-new',
                            'python-fu-console'
                        ]
                        for proc_name in test_procs:
                            try:
                                proc = pdb.lookup_procedure(proc_name)
                                sample_procedures.append({
                                    "name": proc_name,
                                    "available": proc is not None,
                                    "type": str(type(proc)) if proc else None
                                })
                            except Exception:
                                sample_procedures.append({
                                    "name": proc_name,
                                    "available": False,
                                    "error": "lookup_failed"
                                })
                    except Exception as proc_error:
                        print(f"Error testing procedures: {proc_error}")

                    pdb_info["sample_procedures"] = sample_procedures

                gimp_info["pdb"] = pdb_info

            except Exception as pdb_error:
                print(f"Error getting PDB info: {pdb_error}")
                gimp_info["pdb"] = {"error": str(pdb_error)}

            try:
                capabilities = {
                    "has_python_console": True,
                    "mcp_server_running": True,
                    "supports_image_export": True,
                    "supports_metadata_export": True,
                    "supports_gimp_info": True,
                    "api_version": "3.0+",
                    "python_version": sys.version,
                    "available_modules": [],
                    "gimp_module_attributes": len(dir(Gimp)),
                    "gimp_methods": [
                        attr for attr in dir(Gimp)
                        if callable(getattr(Gimp, attr, None))
                    ][:20]
                }

                test_modules = [
                    'gi.repository.Gimp',
                    'gi.repository.Gegl',
                    'gi.repository.Gio',
                    'json',
                    'base64',
                    'tempfile'
                ]
                for module_name in test_modules:
                    try:
                        if module_name == 'gi.repository.Gimp':
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        elif module_name == 'gi.repository.Gegl':
                            from gi.repository import Gegl  # noqa: F401
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        elif module_name == 'gi.repository.Gio':
                            from gi.repository import Gio  # noqa: F401
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        else:
                            __import__(module_name)
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                    except ImportError:
                        capabilities["available_modules"].append(
                            {"name": module_name, "available": False}
                        )
                    except Exception as mod_error:
                        capabilities["available_modules"].append(
                            {"name": module_name, "available": False, "error": str(mod_error)}
                        )

                gimp_info["capabilities"] = capabilities

            except Exception as cap_error:
                print(f"Error getting capabilities: {cap_error}")
                gimp_info["capabilities"] = {"error": str(cap_error)}

            try:
                system_info = {
                    "platform": platform.platform(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_version": platform.python_version(),
                    "environment_vars": {
                        "HOME": os.environ.get("HOME"),
                        "USER": os.environ.get("USER"),
                        "GIMP_PLUG_IN_DIR": os.environ.get("GIMP_PLUG_IN_DIR"),
                        "GIMP_DATA_DIR": os.environ.get("GIMP_DATA_DIR")
                    }
                }

                gimp_info["system"] = system_info

            except Exception as sys_error:
                print(f"Error getting system info: {sys_error}")
                gimp_info["system"] = {"error": str(sys_error)}

            return {
                "status": "success",
                "results": gimp_info
            }

        except Exception as e:
            error_msg = f"Error getting GIMP info: {str(e)}\n{traceback.format_exc()}"
            return {
                "status": "error",
                "error": error_msg,
                "traceback": traceback.format_exc()
            }
    

    def _get_context_state(self):
        """Get current GIMP context state (colors, brush, tool settings)."""
        try:
            print("Getting GIMP context state...")

            context_state = {}

            try:
                fg_color = Gimp.context_get_foreground()
                bg_color = Gimp.context_get_background()

                context_state["foreground_color"] = {
                    "color_object": str(fg_color),
                    "description": "Current foreground color"
                }
                context_state["background_color"] = {
                    "color_object": str(bg_color),
                    "description": "Current background color"
                }

                try:
                    if hasattr(fg_color, 'get_rgba'):
                        rgba = fg_color.get_rgba()
                        context_state["foreground_color"]["rgba"] = list(rgba) if rgba else None
                except Exception as color_error:
                    context_state["foreground_color"]["rgba_error"] = str(color_error)

                try:
                    if hasattr(bg_color, 'get_rgba'):
                        rgba = bg_color.get_rgba()
                        context_state["background_color"]["rgba"] = list(rgba) if rgba else None
                except Exception as color_error:
                    context_state["background_color"]["rgba_error"] = str(color_error)

            except Exception as color_err:
                context_state["colors_error"] = str(color_err)

            try:
                brush = Gimp.context_get_brush()
                if brush:
                    context_state["brush"] = {
                        "name": brush.get_name() if hasattr(brush, 'get_name') else str(brush),
                        "description": "Current brush"
                    }
            except Exception as brush_err:
                context_state["brush_error"] = str(brush_err)

            try:
                opacity = Gimp.context_get_opacity()
                context_state["opacity"] = {
                    "value": opacity,
                    "description": "Current opacity percentage (0-100)"
                }
            except Exception as opacity_err:
                context_state["opacity_error"] = str(opacity_err)

            try:
                paint_mode = Gimp.context_get_paint_mode()
                context_state["paint_mode"] = {
                    "value": str(paint_mode),
                    "description": "Current paint/blend mode"
                }
            except Exception as mode_err:
                context_state["paint_mode_error"] = str(mode_err)

            try:
                feather = Gimp.context_get_feather()
                feather_radius = Gimp.context_get_feather_radius()
                context_state["feather"] = {
                    "enabled": feather,
                    "radius": feather_radius,
                    "description": "Selection feathering state"
                }
            except Exception as feather_err:
                context_state["feather_note"] = "Feather settings not available in context"

            try:
                antialias = Gimp.context_get_antialias()
                context_state["antialias"] = {
                    "enabled": antialias,
                    "description": "Antialiasing state for selections"
                }
            except Exception as aa_err:
                context_state["antialias_note"] = "Antialias setting not available"

            return {
                "status": "success",
                "results": context_state
            }

        except Exception as e:
            error_msg = f"Error getting context state: {str(e)}\n{traceback.format_exc()}"
            return {
                "status": "error",
                "error": error_msg,
                "traceback": traceback.format_exc()
    
          }
    
#25
    def _apply_gegl_to_drawable(self, drawable, op_name: str, props: dict):
        """
        Applique une opération GEGL de manière DESTRUCTIVE et fiable
        (buffer -> shadow -> merge_shadow).
        """
        from gi.repository import Gegl, Gimp

        src_buffer = drawable.get_buffer()
        shadow_buffer = drawable.get_shadow_buffer()

        graph = Gegl.Node()
        src = graph.create_child("gegl:buffer-source")
        src.set_property("buffer", src_buffer)

        op = graph.create_child(op_name)
        for k, v in (props or {}).items():
            op.set_property(k, v)

        sink = graph.create_child("gegl:buffer-sink")
        sink.set_property("buffer", shadow_buffer)

        src.link(op)
        op.link(sink)

        sink.process()

        drawable.merge_shadow(True)
        drawable.update(0, 0, drawable.get_width(), drawable.get_height())
        Gimp.displays_flush()
    def _brightness_contrast(self, drawable, brightness: float = 0.0, contrast: float = 0.0):
        """
        brightness et contrast attendus dans [-1.0, +1.0] (GEGL).
        """
        props = {
            "brightness": float(brightness),
            "contrast": float(contrast),
        }
        self._apply_gegl_to_drawable(drawable, "gegl:brightness-contrast", props)
        


    def _bw_grayscale(self, drawable):
        # noir et blanc
        self._apply_gegl_to_drawable(drawable, "gegl:gray", {})
#26
    def _debug_list_gauss_procs(self):
        from gi.repository import Gimp
        pdb = Gimp.get_pdb()

        # EXACTEMENT 8 paramètres explicites
        procs = pdb.query_procedures(
            "gauss",   # name
            "",        # blurb
            "",        # help
            "",        # author
            "",        # copyright
            "",        # date
            "",        # type
            ""         # path
        )

        print("\n=== GAUSS PROCEDURES ===")
        for p in procs:
            print(" -", p)
        print("=======================\n")

    #07
    

    def _safe_file_save(self, image, drawable, path):
        """
        Export PNG robuste compatible TOUS builds GIMP 3
        (drawable / drawables / aucun)
        """
        from gi.repository import Gimp, Gio

        file_obj = Gio.File.new_for_path(path)
        pdb = Gimp.get_pdb()

        export_proc = pdb.lookup_procedure("file-png-export")
        if not export_proc:
            raise RuntimeError("file-png-export not available")

        cfg = export_proc.create_config()

        # 🔑 Propriétés toujours valides
        cfg.set_property("image", image)
        cfg.set_property("file", file_obj)

        # 🔑 drawable OU drawables selon le build
        if cfg.find_property("drawable"):
            cfg.set_property("drawable", drawable)
        elif cfg.find_property("drawables"):
            cfg.set_property("drawables", [drawable])
        else:
            # OK : certains builds n’en veulent aucun
            pass

        # Options PNG sûres
        for k, v in {
            "compression": 9,
            "interlaced": False,
            "save-transparency": True,
            "save-color-profile": False,
            "save-exif": False,
            "save-xmp": False,
            "save-iptc": False,
        }.items():
            if cfg.find_property(k):
                cfg.set_property(k, v)

        print(f"[PNG EXPORT] {path}")
        export_proc.run(cfg)


     #08
    def _safe_png_export(self, image, drawable, path):
        from gi.repository import Gimp, Gio

        file_obj = Gio.File.new_for_path(path)
        pdb = Gimp.get_pdb()

        export_proc = pdb.lookup_procedure("file-png-export")
        if not export_proc:
            raise RuntimeError("file-png-export not available")

        cfg = export_proc.create_config()

        # Propriétés toujours valides
        cfg.set_property("image", image)
        cfg.set_property("file", file_obj)

        # 🔑 GIMP 3 : drawable OU drawables selon le build
        if cfg.find_property("drawable"):
            cfg.set_property("drawable", drawable)
        elif cfg.find_property("drawables"):
            cfg.set_property("drawables", [drawable])
        else:
            # certains builds n’en veulent aucun → OK
            pass

        # Options PNG sûres
        for k, v in {
            "compression": 9,
            "interlaced": False,
            "save-transparency": True,
            "save-color-profile": False,
            "save-exif": False,
            "save-xmp": False,
            "save-iptc": False,
        }.items():
            if cfg.find_property(k):
                cfg.set_property(k, v)

        export_proc.run(cfg)
    
    def _execute_actions(self, actions):
        """
        Reçoit une liste d'actions de haut niveau (Agent SML / Vision)
        et les exécute sur l'image courante dans GIMP 3.
        """
        import traceback
        import base64
        import tempfile
        import os
        from gi.repository import Gimp, Gio, GObject

        try:
            # -------------------------------------------------------
            # Vérifier qu'une image est ouverte
            # -------------------------------------------------------
            images = Gimp.get_images()
            if not images:
                return {"status": "error", "error": "No images open for execute_actions"}

            image = images[0]
            drawables = image.get_selected_drawables()
            if not drawables:
                return {"status": "error", "error": "No selected drawable in image"}

            drawable = drawables[0]


            executed = []

            # =======================================================
            # Boucle principale des actions
            # =======================================================
            for act in actions:
                name = act.get("action")
                params = act.get("params", {}) or {}

                # ===================================================
                # 1) apply_filter
                # ===================================================
                if name == "apply_filter":
                    filter_name = params.get("filter")

                    if filter_name == "gaussian_blur":
                        radius = float(params.get("radius", 5.0))
                        pf = {"filter": "gaussian_blur", "radius": radius}

                        self._apply_filter(pf, image=image, drawable=drawable)

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": f"gaussian_blur radius={radius}"
                        })
                        continue

                    elif filter_name == "posterize":
                        levels = int(params.get("levels", 4))
                        pf = {"filter": "posterize", "levels": levels}

                        self._apply_filter(pf, image=image, drawable=drawable)

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": f"posterize levels={levels}"
                        })
                        continue


                    elif filter_name in ("desaturate", "grayscale", "bw", "noir_et_blanc"):
                        pf = {"filter": "desaturate"}
                        self._apply_filter(pf, image=image, drawable=drawable)

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": "desaturate applied via PDB"
                        })
                        continue
                    elif filter_name in ("hue_saturation", "hue-saturation"):
                        from gi.repository import Gegl, Gimp

                        hue = float(params.get("hue", 0))            # -180..180 ou 0..360
                        saturation = float(params.get("saturation", 0))  # -100..100
                        lightness = float(params.get("lightness", 0))     # -100..100

                        # Normalisation GEGL
                        hue_norm = hue / 360.0
                        sat_norm = saturation / 100.0
                        light_norm = lightness / 100.0

                        src_buffer = drawable.get_buffer()
                        shadow_buffer = drawable.get_shadow_buffer()

                        graph = Gegl.Node()

                        src = graph.create_child("gegl:buffer-source")
                        src.set_property("buffer", src_buffer)

                        hs = graph.create_child("gegl:hue-saturation")
                        hs.set_property("hue", hue_norm)
                        hs.set_property("saturation", sat_norm)
                        hs.set_property("lightness", light_norm)

                        sink = graph.create_child("gegl:buffer-sink")
                        sink.set_property("buffer", shadow_buffer)

                        src.link(hs)
                        hs.link(sink)

                        sink.process()

                        drawable.merge_shadow(True)
                        drawable.update(0, 0, drawable.get_width(), drawable.get_height())
                        Gimp.displays_flush()

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": f"gegl:hue-saturation applied (h={hue}, s={saturation}, l={lightness})"
                        })
                        continue

               #

# ===================================================
# 6) 🎨 Colorisation (Couleurs → Colorier) — GIMP 3
# ===================================================
               
                elif name == "enhance":
                    # params: {"brightness": 0.2} ou {"contrast": 0.3}
                    b = params.get("brightness")
                    c = params.get("contrast")

                    if b is None and c is None:
                        executed.append({
                            "action": name,
                            "status": "skipped",
                            "reason": "enhance requires brightness or contrast"
                        })
                        continue

                    brightness = float(b) if b is not None else 0.0
                    contrast = float(c) if c is not None else 0.0

                    self._brightness_contrast(drawable, brightness=brightness, contrast=contrast)

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": f"brightness={brightness}, contrast={contrast}"
                    })
                    continue
    
                
  

                # ===================================================
                # 2) Sélection rectangle (fallback / debug)
                # ===================================================
                elif name == "select_rectangle":
                    x = int(params.get("x", 0))
                    y = int(params.get("y", 0))
                    w = int(params.get("width", 0))
                    h = int(params.get("height", 0))

                    Gimp.Image.select_rectangle(
                        image,
                        Gimp.ChannelOps.REPLACE,
                        x, y, w, h
                    )

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": f"rectangle x={x}, y={y}, w={w}, h={h}"
                    })
                    continue

                # ===================================================
                # 3) 🔥 Sélection par MASQUE PNG (SAM) — CORRIGÉ
                # ===================================================
                #janvier le 30
                elif name == "select_mask_png":
                    import base64, tempfile, os
                    from gi.repository import Gimp, Gio

                    png_b64 = params.get("png_b64")
                    offset_x = int(params.get("offset_x", 0))
                    offset_y = int(params.get("offset_y", 0))

                    if not png_b64 or len(png_b64) < 100:
                        raise RuntimeError("png_b64 missing or too small")

                    # -------------------------------------------------------
                    # 1) Base64 → PNG
                    # -------------------------------------------------------
                    png_data = base64.b64decode(png_b64)

                    fd, mask_path = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    with open(mask_path, "wb") as f:
                        f.write(png_data)

                    # -------------------------------------------------------
                    # 2) Charger le PNG comme calque
                    # -------------------------------------------------------
                    pdb = Gimp.get_pdb()
                    proc = pdb.lookup_procedure("gimp-file-load-layer")
                    cfg = proc.create_config()
                    cfg.set_property("image", image)
                    cfg.set_property("file", Gio.File.new_for_path(mask_path))

                    result = proc.run(cfg)
                    mask_layer = result.index(1)

                    image.insert_layer(mask_layer, None, 0)

                    # Sélection depuis l’alpha
                    Gimp.Image.select_item(
                        image,
                        Gimp.ChannelOps.REPLACE,
                        mask_layer
                    )

                    # 🔥 SUPPRESSION IMMÉDIATE DU CALQUE MASQUE
                    image.remove_layer(mask_layer)

                    Gimp.displays_flush()

                    if offset_x or offset_y:
                        Gimp.Selection.translate(image, offset_x, offset_y)

                    Gimp.displays_flush()

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": "mask selection applied (GIMP 3 FINAL STABLE)"
                    })
                    continue





                 
                                # ===================================================
                # 🎨 apply_colorize_on_selection (GIMP 3 - STABLE)
                # Recolore UNIQUEMENT la zone sélectionnée
                # ===================================================
                elif name == "apply_colorize_on_selection":
                    from gi.repository import Gimp

                    hue = float(params.get("hue", 0.0))              # 0..360
                    saturation = float(params.get("saturation", 50)) # -100..100 (ou 0..100 selon usage)
                    lightness = float(params.get("lightness", 0))    # -100..100

                    # (optionnel mais recommandé) vérifier qu'il y a une sélection non vide
                    try:
                        has_sel, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
                        if not has_sel or (x2 - x1) <= 1 or (y2 - y1) <= 1:
                            executed.append({
                                "action": name,
                                "status": "skipped",
                                "details": "no active selection"
                            })
                            continue
                    except Exception:
                        # si bounds n'est pas dispo, on tente quand même
                        pass

                    pdb = Gimp.get_pdb()

                    # Procédure la plus stable (Colorize HSL)
                    # NOTE: selon build, le nom peut varier légèrement, on essaie plusieurs.
                    proc_names = [
                        "gimp-drawable-colorize-hsl",
                        "gimp-drawable-colorize",       # fallback
                    ]

                    proc = None
                    for pn in proc_names:
                        try:
                            proc = pdb.lookup_procedure(pn)
                            if proc is not None:
                                proc_name_used = pn
                                break
                        except Exception:
                            proc = None

                    if proc is None:
                        raise RuntimeError("No PDB colorize procedure found (expected gimp-drawable-colorize-hsl).")

                    cfg = proc.create_config()

                    # Les propriétés peuvent varier selon la procédure -> on set seulement si dispo
                    # 1) drawable
                    for key in ("drawable", "layer", "item"):
                        try:
                            cfg.set_property(key, drawable)
                            break
                        except Exception:
                            pass

                    # 2) hue/sat/light
                    # gimp-drawable-colorize-hsl attend généralement H(0..360), S(-100..100), L(-100..100)
                    for (k, v) in (("hue", hue), ("saturation", saturation), ("lightness", lightness)):
                        try:
                            cfg.set_property(k, v)
                        except Exception:
                            # fallback noms alternatifs
                            if k == "saturation":
                                for alt in ("sat", "s"):
                                    try:
                                        cfg.set_property(alt, v)
                                        break
                                    except Exception:
                                        pass
                            if k == "lightness":
                                for alt in ("light", "l"):
                                    try:
                                        cfg.set_property(alt, v)
                                        break
                                    except Exception:
                                        pass

                    proc.run(cfg)
                    Gimp.displays_flush()

                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": f"colorize applied on selection via {proc_name_used} (h={hue}, s={saturation}, l={lightness})"
                    })
                    continue












                                # ===================================================
                # 🔥 SMART INPAINT (GIMP ↔ LaMa)
                # ===================================================
# 🔥 SMART INPAINT (GIMP ↔ LaMa) — FIXED
# ===================================================
                elif name == "smart_inpaint":
                    import subprocess, tempfile, os
                    from gi.repository import Gimp, Gio, Gegl

                    try:
                        tmp_dir = tempfile.mkdtemp(prefix="gimp_lama_")
                        img_path  = os.path.join(tmp_dir, "image.png")
                        mask_path = os.path.join(tmp_dir, "mask.png")
                        out_path  = os.path.join(tmp_dir, "out.png")

                        print("[SMART_INPAINT] START")

                        # =================================================
                        # 1) EXPORT IMAGE RGB
                        # =================================================
                        self._safe_png_export(image, drawable, img_path)

                        # =================================================
                        # 2) MASQUE = CALQUE RGBA (NOIR / BLANC)
                        # =================================================
                        mask_layer = Gimp.Layer.new(
                            image,
                            "lama_mask",
                            image.get_width(),
                            image.get_height(),
                            Gimp.ImageType.RGBA_IMAGE,   # 🔑 PAS GRAY
                            100,
                            Gimp.LayerMode.NORMAL
                        )
                        image.insert_layer(mask_layer, None, 0)

                        # fond noir
                        Gimp.context_set_foreground(Gegl.Color.new("black"))
                        mask_layer.fill(Gimp.FillType.FOREGROUND)

                        # zone blanche = sélection
                        Gimp.context_set_foreground(Gegl.Color.new("white"))
                        mask_layer.edit_fill(Gimp.FillType.FOREGROUND)

                        # =================================================
                        # 3) EXPORT DU MASQUE
                        # =================================================
                        self._safe_png_export(image, mask_layer, mask_path)

                        image.remove_layer(mask_layer)

                        # =================================================
                        # 4) LANCER LaMa
                        # =================================================
                        subprocess.check_call([
                            "python3",
                            "/home/el-ismaiyly/gimp-mcp/pipeline/opencv_inpaint_final.py",
                            "--image", img_path,
                            "--mask", mask_path,
                            "--out", out_path
                        ])

                        if not os.path.exists(out_path):
                            raise RuntimeError("LaMa output missing")

                        # =================================================
                        # 5) IMPORT RÉSULTAT
                        # =================================================
                        new_layer = Gimp.file_load_layer(
                            Gimp.RunMode.NONINTERACTIVE,
                            image,
                            Gio.File.new_for_path(out_path)
                        )
                        image.insert_layer(new_layer, None, 0)

                        drawable.set_visible(False)
                        image.set_active_layer(new_layer)
                        Gimp.displays_flush()

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": "smart_inpaint OK"
                        })
                        continue

                    except Exception as e:
                        executed.append({
                            "action": name,
                            "status": "error",
                            "error": str(e)
                        })
                        continue


                    # ============================================================
                    # Helpers (compat GIMP 3 / fallback PDB)
                    # ============================================================
                    def _dup_layer(layer):
                        """Duplique un calque en essayant copy(), sinon PDB."""
                        try:
                            return layer.copy()
                        except Exception:
                            pdb = Gimp.get_pdb()
                            proc = pdb.lookup_procedure("gimp-layer-copy")
                            if not proc:
                                raise RuntimeError("Cannot duplicate layer: gimp-layer-copy missing")
                            cfg = proc.create_config()
                            cfg.set_property("layer", layer)
                            # certaines builds ont add-alpha / add_mask, on ignore si absent
                            if cfg.find_property("add-alpha"):
                                cfg.set_property("add-alpha", True)
                            res = proc.run(cfg)
                            try:
                                return res.index(1)
                            except Exception:
                                raise RuntimeError("Cannot duplicate layer (unknown return from gimp-layer-copy)")

                    def _add_mask_from_selection(layer):
                        """Ajoute un masque basé sur la sélection (API si dispo, sinon PDB)."""
                        try:
                            mask_obj = layer.create_mask(Gimp.AddMaskType.SELECTION)
                            layer.add_mask(mask_obj)
                            return
                        except Exception:
                            pdb = Gimp.get_pdb()
                            proc = pdb.lookup_procedure("gimp-layer-create-mask")
                            proc2 = pdb.lookup_procedure("gimp-layer-add-mask")
                            if (not proc) or (not proc2):
                                raise RuntimeError("Mask procedures unavailable (create/add mask)")
                            cfg = proc.create_config()
                            cfg.set_property("layer", layer)
                            cfg.set_property("add-mask-type", int(Gimp.AddMaskType.SELECTION))
                            out = proc.run(cfg)
                            try:
                                created_mask = out.index(1)
                            except Exception:
                                created_mask = None
                            if created_mask is None:
                                raise RuntimeError("Could not create mask from selection")
                            cfg2 = proc2.create_config()
                            cfg2.set_property("layer", layer)
                            cfg2.set_property("mask", created_mask)
                            proc2.run(cfg2)

                    def _select_from_mask_png(mask_png_path):
                        """Charge mask.png en calque temp, sélection = alpha, puis supprime le calque."""
                        tmp_mask_layer = Gimp.file_load_layer(
                            Gimp.RunMode.NONINTERACTIVE,
                            image,
                            Gio.File.new_for_path(mask_png_path)
                        )
                        image.insert_layer(tmp_mask_layer, None, 0)
                        # alpha -> sélection
                        Gimp.Image.select_item(image, Gimp.ChannelOps.REPLACE, tmp_mask_layer)
                        image.remove_layer(tmp_mask_layer)

                    def _safe_selection_none():
                        try:
                            Gimp.Selection.none(image)
                        except Exception:
                            pass

                    def _safe_selection_grow(px):
                        if px and px > 0:
                            try:
                                Gimp.Selection.grow(image, int(px))
                            except Exception:
                                pass

                    def _safe_selection_feather(px):
                        if px and px > 0:
                            try:
                                Gimp.Selection.feather(image, int(px))
                            except Exception:
                                pass

                    def _save_selection_to_channel(name_hint):
                        """Sauvegarde la sélection dans un channel temporaire et le renvoie."""
                        pdb = Gimp.get_pdb()
                        proc = pdb.lookup_procedure("gimp-selection-save")
                        if not proc:
                            return None
                        cfg = proc.create_config()
                        cfg.set_property("image", image)
                        out = proc.run(cfg)
                        ch = None
                        try:
                            ch = out.index(1)
                        except Exception:
                            ch = None
                        try:
                            if ch and hasattr(ch, "set_name"):
                                ch.set_name(name_hint)
                        except Exception:
                            pass
                        return ch

                    def _remove_channel(ch):
                        try:
                            pdb = Gimp.get_pdb()
                            proc = pdb.lookup_procedure("gimp-image-remove-channel")
                            if proc and ch:
                                cfg = proc.create_config()
                                cfg.set_property("image", image)
                                cfg.set_property("channel", ch)
                                proc.run(cfg)
                        except Exception:
                            pass

                    # =================================================
                    # START
                    # =================================================
                    try:
                        tmp_dir = tempfile.mkdtemp(prefix="gimp_lama_")
                        img_path  = os.path.join(tmp_dir, "image.png")
                        mask_path = os.path.join(tmp_dir, "mask.png")
                        out_path  = os.path.join(tmp_dir, "out.png")

                        print("[SMART_INPAINT] START (QUALITY MAX)")

                        # =================================================
                        # 0) Vérif sélection
                        # =================================================
                        if Gimp.Selection.is_empty(image):
                            raise RuntimeError("Selection is empty: nothing to inpaint")

                        # =================================================
                        # 1) EXPORT IMAGE RGB
                        # =================================================
                        self._safe_png_export(image, drawable, img_path)

                        # =================================================
                        # 2) MASQUE = CALQUE RGBA (NOIR / BLANC)
                        # =================================================
                        mask_layer = Gimp.Layer.new(
                            image,
                            "lama_mask",
                            image.get_width(),
                            image.get_height(),
                            Gimp.ImageType.RGBA_IMAGE,
                            100,
                            Gimp.LayerMode.NORMAL
                        )
                        image.insert_layer(mask_layer, None, 0)

                        # fond noir
                        Gimp.context_set_foreground(Gegl.Color.new("black"))
                        mask_layer.fill(Gimp.FillType.FOREGROUND)

                        # zone blanche = sélection active
                        Gimp.context_set_foreground(Gegl.Color.new("white"))
                        mask_layer.edit_fill(Gimp.FillType.FOREGROUND)

                        # =================================================
                        # 3) EXPORT DU MASQUE
                        # =================================================
                        self._safe_png_export(image, mask_layer, mask_path)
                        image.remove_layer(mask_layer)

                        # =================================================
                        # 4) LANCER LaMa
                        # =================================================
                        subprocess.check_call([
                            "python3",
                            "/home/el-ismaiyly/gimp-mcp/pipeline/opencv_inpaint_final.py",
                            "--image", img_path,
                            "--mask",  mask_path,
                            "--out",   out_path
                        ])

                        if not os.path.exists(out_path):
                            raise RuntimeError("LaMa output missing")

                        # =================================================
                        # 5) IMPORT RÉSULTAT (nouveau calque)
                        # =================================================
                        new_layer = Gimp.file_load_layer(
                            Gimp.RunMode.NONINTERACTIVE,
                            image,
                            Gio.File.new_for_path(out_path)
                        )
                        new_layer.set_name("lama_result")
                        image.insert_layer(new_layer, None, 0)

                        if HIDE_OLD:
                            try:
                                drawable.set_visible(False)
                            except Exception:
                                pass

                        # ============================================================
                        # 6) QUALITÉ MAX — PASS A : HARMONISATION LUMIÈRE (LOW FREQ)
                        # ============================================================
                        pdb = Gimp.get_pdb()

                        _safe_selection_none()
                        _select_from_mask_png(mask_path)
                        _safe_selection_grow(GROW_LARGE)
                        _safe_selection_feather(FEATHER_LARGE)

                        low_layer = _dup_layer(new_layer)
                        low_layer.set_name("blend_lowfreq")
                        image.insert_layer(low_layer, None, 0)

                        # Flou large via PDB (REMPLACE GEGL)
                        blur = pdb.lookup_procedure("plug-in-gauss")
                        if blur:
                            cfg = blur.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", low_layer)
                            cfg.set_property("horizontal", 18.0)
                            cfg.set_property("vertical", 18.0)
                            cfg.set_property("method", 0)
                            blur.run(cfg)

                        # Fusion lumière (ANTI-VOILE)
                        try:
                            low_layer.set_mode(Gimp.LayerMode.LCH_LIGHTNESS)
                            low_layer.set_opacity(55.0)   # ⬅ CRITIQUE (ne pas dépasser 60)
                        except Exception:
                            low_layer.set_mode(Gimp.LayerMode.SOFTLIGHT)
                            low_layer.set_opacity(22.0)

                        _add_mask_from_selection(low_layer)

                        # ============================================================
                        # 7) MICRO-CONTRASTE (UNSHARP MASK STABLE)
                        # ============================================================
                        _safe_selection_none()
                        _select_from_mask_png(mask_path)
                        _safe_selection_grow(GROW_FINE)
                        _safe_selection_feather(FEATHER_FINE)

                        hp_layer = _dup_layer(new_layer)
                        hp_layer.set_name("microcontrast")
                        image.insert_layer(hp_layer, None, 0)

                        sharp = pdb.lookup_procedure("plug-in-unsharp-mask")
                        if sharp:
                            cfg = sharp.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", hp_layer)
                            cfg.set_property("radius", 3.0)
                            cfg.set_property("amount", 0.6)
                            cfg.set_property("threshold", 0)
                            sharp.run(cfg)

                        try:
                            hp_layer.set_mode(Gimp.LayerMode.OVERLAY)
                            hp_layer.set_opacity(8.0)
                        except Exception:
                            pass

                        _add_mask_from_selection(hp_layer)

                        # ============================================================
                        # 8) GRAIN PHOTO DOUX (RGB NOISE)
                        # ============================================================
                        grain_layer = _dup_layer(new_layer)
                        grain_layer.set_name("photo_grain")
                        image.insert_layer(grain_layer, None, 0)

                        noise = pdb.lookup_procedure("plug-in-rgb-noise")
                        if noise:
                            cfg = noise.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", grain_layer)
                            cfg.set_property("correlated", True)
                            cfg.set_property("independent", False)
                            cfg.set_property("red", 0.03)
                            cfg.set_property("green", 0.03)
                            cfg.set_property("blue", 0.03)
                            cfg.set_property("alpha", 0.0)
                            noise.run(cfg)

                        # Flou léger du grain
                        if blur:
                            cfg = blur.create_config()
                            cfg.set_property("image", image)
                            cfg.set_property("drawable", grain_layer)
                            cfg.set_property("horizontal", 1.2)
                            cfg.set_property("vertical", 1.2)
                            cfg.set_property("method", 0)
                            blur.run(cfg)

                        try:
                            grain_layer.set_mode(Gimp.LayerMode.SOFTLIGHT)
                            grain_layer.set_opacity(10.0)
                        except Exception:
                            pass

                        _add_mask_from_selection(grain_layer)

                        # ============================================================
                        # 9) BONUS — TEXTURE BORROWING EN ANNEAU
                        # ============================================================
                        if ENABLE_RING_TEXTURE:
                            try:
                                _safe_selection_none()
                                _select_from_mask_png(mask_path)
                                _safe_selection_grow(RING_OUTER_GROW)
                                _safe_selection_feather(RING_FEATHER)
                                outer = _save_selection_to_channel("ring_outer")

                                _safe_selection_none()
                                _select_from_mask_png(mask_path)
                                _safe_selection_grow(RING_INNER_GROW)
                                _safe_selection_feather(int(RING_FEATHER * 0.6))
                                inner = _save_selection_to_channel("ring_inner")

                                Gimp.Image.select_item(image, Gimp.ChannelOps.REPLACE, outer)
                                Gimp.Image.select_item(image, Gimp.ChannelOps.SUBTRACT, inner)

                                tex_layer = _dup_layer(drawable)
                                tex_layer.set_name("ring_texture")
                                image.insert_layer(tex_layer, None, 0)

                                # Flou directionnel doux
                                if blur:
                                    cfg = blur.create_config()
                                    cfg.set_property("image", image)
                                    cfg.set_property("drawable", tex_layer)
                                    cfg.set_property("horizontal", 6.0)
                                    cfg.set_property("vertical", 2.0)
                                    cfg.set_property("method", 0)
                                    blur.run(cfg)

                                tex_layer.set_mode(Gimp.LayerMode.OVERLAY)
                                tex_layer.set_opacity(RING_OPACITY)

                                _add_mask_from_selection(tex_layer)

                                _remove_channel(outer)
                                _remove_channel(inner)

                            except Exception as e:
                                print("[QUALITY_MAX] Ring texture skipped:", e)

                        # Nettoyage sélection
                        _safe_selection_none()

                        # refresh
                        try:
                            new_layer.update(0, 0, new_layer.get_width(), new_layer.get_height())
                        except Exception:
                            pass
                        try:
                            Gimp.displays_flush()
                        except Exception:
                            pass

                        executed.append({
                            "action": name,
                            "status": "ok",
                            "details": "smart_inpaint QUALITY MAX OK (PDB only)"
                        })
                        continue

                    except Exception as e:
                        executed.append({
                            "action": name,
                            "status": "error",
                            "error": str(e)
                        })
                        continue
                                              
                elif name == "clear_selection":
                    Gimp.Selection.none(image)
                    executed.append({
                        "action": name,
                        "status": "ok",
                        "details": "selection cleared"
                    })
                    continue


                # ===================================================
                # 6) Action inconnue
                # ===================================================
                else:
                    executed.append({
                        "action": name,
                        "status": "skipped",
                        "reason": f"Unknown or unsupported action '{name}'"
                    })
                    continue
                
            # ===================================================
            # 🧹 CLEANUP FINAL DES CALQUES TEMPORAIRES (PNG)
            # ===================================================
            for layer in list(image.get_layers()):
                try:
                    name = layer.get_name()
                    if name and name.startswith("tmp") and name.endswith(".png"):
                        image.remove_layer(layer)
                except Exception:
                    pass

            # -------------------------------------------------------
            # Rafraîchir l’affichage
            # -------------------------------------------------------
            Gimp.displays_flush()
            return {"status": "success", "results": {"executed": executed}}

        except Exception as e:
            tb = traceback.format_exc()
            print("Error in _execute_actions:", e)
            print(tb)
            return {"status": "error", "error": str(e), "traceback": tb}
        ()


    COLOR_NAMES = {
        "red": "#FF0000",
        "green": "#00FF00",
        "blue": "#0000FF",
        "white": "#FFFFFF",
        "black": "#000000",
        "yellow": "#FFFF00",
        "orange": "#FFA500",
        "purple": "#800080"
    }

    def normalize_color(self, col):
        if not col:
            return "#FF0000"
        col = col.lower().strip()
        if col.startswith("#"):
            return col
        if col in self.COLOR_NAMES:
            return self.COLOR_NAMES[col]
        return "#FF0000"

    def hex_to_rgb(self, h):
        h = h.lstrip("#")
        if len(h) != 6:
            raise ValueError("Invalid hex color")
        return (
            int(h[0:2], 16) / 255.0,
            int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0
        )

    def _draw_circle(self, image, drawable, params):
        """
        Dessine un cercle en utilisant une sélection elliptique + stroke.

        params attendus (tous optionnels sauf shape):
          - x : "center" ou coordonnée en pixels (float/int)
          - y : "center" ou coordonnée en pixels
          - radius : rayon en pixels (float/int)
          - stroke_width : épaisseur du trait (float, défaut = 5.0)
          - fill : "none" (par défaut) ou "fill" pour remplir le disque

        La couleur utilisée est la couleur de premier plan actuelle dans GIMP.
        """
        from gi.repository import Gimp

        # Taille de l'image
        width = image.get_width()
        height = image.get_height()

        # --- Centre du cercle ---
        x_param = params.get("x", "center")
        y_param = params.get("y", "center")

        if isinstance(x_param, (int, float)):
            cx = float(x_param)
        else:
            # "center" ou autre → on centre
            cx = width / 2.0

        if isinstance(y_param, (int, float)):
            cy = float(y_param)
        else:
            cy = height / 2.0

        # --- Rayon ---
        radius = float(params.get("radius", min(width, height) / 4.0))
        if radius <= 0:
            radius = min(width, height) / 4.0

        # BBox de l'ellipse
        left = int(cx - radius)
        top = int(cy - radius)
        w = int(2 * radius)
        h = int(2 * radius)

        # Clamp pour rester dans l'image
        if left < 0:
            left = 0
        if top < 0:
            top = 0
        if left + w > width:
            w = width - left
        if top + h > height:
            h = height - top

        # Option : épaisseur du trait
        stroke_width = float(params.get("stroke_width", 5.0))

        # Option : remplissage ou juste contour
        fill_mode = params.get("fill", "none")  # "none" ou "fill"

        # --- Dessin dans GIMP ---
        # On pousse le contexte pour ne pas casser les réglages de l'utilisateur
        Gimp.context_push()
        try:
            # Régler l'épaisseur du pinceau (si possible)
            try:
                Gimp.context_set_brush_size(stroke_width)
            except Exception as e:
                print("Warning: cannot set brush size:", e)

            # Créer une sélection elliptique
            image.select_ellipse(
                Gimp.ChannelOps.REPLACE,
                left,
                top,
                w,
                h
            )

            # Remplir si demandé
            if fill_mode == "fill":
                try:
                    Gimp.edit_fill(drawable, Gimp.FillType.FOREGROUND)
                except Exception as e:
                    print("Warning: edit_fill failed:", e)

            # Tracer le contour
            try:
                Gimp.edit_stroke(drawable)
            except Exception as e:
                print("Warning: edit_stroke failed:", e)

            # Optionnel : enlever la sélection
            try:
                image.select_none()
            except Exception as e:
                print("Warning: cannot clear selection:", e)

        finally:
            # Restaurer le contexte
            Gimp.context_pop()
            
    def _adjust_brightness(self, drawable, delta):
        """Ajuste la luminosité."""
        from gi.repository import Gimp
        Gimp.color_brightness_contrast(drawable, delta, 0)
    def _adjust_contrast(self, drawable, delta):
        """Ajuste le contraste."""
        from gi.repository import Gimp
        Gimp.color_brightness_contrast(drawable, 0, delta)
    def _desaturate(self, drawable):
        from gi.repository import Gimp
        gef = Gimp.DrawableFilter.new(drawable, "gegl:gray", "")
        gef.update()
        drawable.append_filter(gef)
        Gimp.displays_flush()




    


Gimp.main(MCPPlugin.__gtype__, sys.argv)






#et 
import json
import requests
import base64
import io
from PIL import Image
import numpy as np
import torch
from segment_anything import sam_model_registry, SamPredictor


from local_sml_agent import call_sml, semantic_analysis
from send_actions_to_gimp import execute_actions


# ============================================================
# 👁️ CONFIG AGENT VISION
# ============================================================

VISION_AGENT_CARD_URL = "http://localhost:8000/.well-known/agent-card.json"


def discover_vision_agent():
    r = requests.get(VISION_AGENT_CARD_URL, timeout=5)
    r.raise_for_status()
    return r.json()


VISION_AGENT = discover_vision_agent()
print("👁️ Agent vision découvert :", VISION_AGENT["name"])


# ============================================================
# 🖼️ IMAGE
# ============================================================

IMAGE_PATH = "/home/el-ismaiyly/Images/JJ.jpg"

with Image.open(IMAGE_PATH).convert("RGB") as img:
    IMAGE_WIDTH, IMAGE_HEIGHT = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    IMAGE_BASE64 = base64.b64encode(buf.getvalue()).decode()
  


print(f"🖼️ Image chargée : {IMAGE_PATH} ({IMAGE_WIDTH}x{IMAGE_HEIGHT})")
# ============================================================
# 🧠 SAM (Segment Anything) — chargé une seule fois
# ============================================================
SAM_CKPT = "/home/el-ismaiyly/gimp-mcp/Segmentation_Instance/sam_vit_h_4b8939.pth"
SAM_MODEL_TYPE = "vit_h"

_sam = sam_model_registry[SAM_MODEL_TYPE](checkpoint=SAM_CKPT)
_sam.to("cuda" if torch.cuda.is_available() else "cpu")
_sam_predictor = SamPredictor(_sam)
print("🧠 SAM prêt:", SAM_MODEL_TYPE, "| device:", ("cuda" if torch.cuda.is_available() else "cpu"))
  


# ============================================================
# 👁️ APPEL AGENT VISION (MULTI-INSTANCES)
# ============================================================

def call_vision_agent(agent_card, image_b64, target,
                      multi=False, max_instances=5, conf=0.25):
    url = agent_card["serviceUrl"]
    payload = {
        "skill": "segment_object",
        "params": {
            "target": target,
            "multi": bool(multi),
            "max_instances": int(max_instances),
            "conf": float(conf),
        },
        "image": image_b64
    }
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def get_instances_from_vision(vision_result):
    """
    Normalise TOUS les formats de l'agent vision
    → retourne TOUJOURS une liste d'instances
    """

    if not isinstance(vision_result, dict):
        raise ValueError(f"Vision result invalide: {type(vision_result)}")

    # --- Cas 1 : instances à la racine ---
    if "instances" in vision_result and isinstance(vision_result["instances"], list):
        return vision_result["instances"]

    # --- Cas 2 : result.instances ---
    if (
        "result" in vision_result
        and isinstance(vision_result["result"], dict)
        and "instances" in vision_result["result"]
        and isinstance(vision_result["result"]["instances"], list)
    ):
        return vision_result["result"]["instances"]

    # --- Cas 3 : UNE SEULE instance dans result ---
    if (
        "result" in vision_result
        and isinstance(vision_result["result"], dict)
        and all(k in vision_result["result"] for k in ("bbox", "mask"))
    ):
        # 🔥 NORMALISATION : on met l'instance dans une liste
        return [vision_result["result"]]

    # --- Cas inconnu ---
    raise ValueError(
        "Format agent vision inconnu.\n"
        f"Clés racine reçues: {list(vision_result.keys())}\n"
        f"Clés result reçues: {list(vision_result.get('result', {}).keys())}"
    )




# ============================================================
# 🧩 LOGIQUE GAUCHE / DROITE
# ============================================================

def pick_instance_by_side(instances, side, image_width):
    """
    Choisit l'instance la plus à gauche ou à droite
    selon la position du centre de la bbox.
    """
    if not instances:
        raise ValueError("Aucune instance détectée")

    scored = []
    for inst in instances:
        b = inst["bbox"]
        cx = b["x"] + (b["width"] / 2.0)
        scored.append((cx, inst))

    scored.sort(key=lambda t: t[0])  # gauche → droite

    if side == "gauche":
        return scored[0][1]
    if side == "droite":
        return scored[-1][1]

    raise ValueError("side doit être 'gauche' ou 'droite'")


# ============================================================
# 🔧 ACTIONS GIMP
# ============================================================

def normalize_actions_for_gimp(actions):
    """
    Supprime les actions non supportées par GIMP.
    """
    return [a for a in actions if a.get("action") != "remove_object"]
#5fev
def _save_debug_from_png_b64(png_b64: str,
                            path_rgba="/tmp/debug_person_mask.png",
                            path_l="/tmp/debug_person_mask_L.png"):
    """
    png_b64 = PNG RGBA (alpha = masque).
    Sauve:
      - RGBA tel quel (ce que GIMP utilise)
      - L = canal alpha (lecture humaine)
    """
    if not png_b64 or len(png_b64) < 100:
        print("[DEBUG] png_b64 empty/too small -> skip debug")
        return

    data = base64.b64decode(png_b64)
    im = Image.open(io.BytesIO(data)).convert("RGBA")

    # 1) RGBA complet
    im.save(path_rgba)

    # 2) Canal alpha -> L
    alpha = im.split()[-1]   # dernier canal = A
    alpha.save(path_l)

    print("[DEBUG] Person masks generated:")
    print(" -", path_l)
    print(" -", path_rgba)

    # Optionnel: ouverture auto
    try:
        import os
        os.system(f"xdg-open {path_l} >/dev/null 2>&1 &")
        os.system(f"xdg-open {path_rgba} >/dev/null 2>&1 &")
    except Exception as e:
        print("[DEBUG] Cannot auto-open:", e)

def build_smart_inpaint_actions(instance):
    bbox = instance["bbox"]
    mask = instance["mask"]

    return [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask["png_b64"],
                "offset_x": bbox["x"],
                "offset_y": bbox["y"]
            },
            "notes": "Sélection de la personne (SAM)"
        },
        {
            "action": "smart_inpaint",
            "target": "image",
            "params": {},
            "notes": "Inpainting via LaMa"
        },
        {
            "action": "clear_selection",
            "target": "selection",
            "params": {}
        }
    ]

def _color_to_hue(color_name: str) -> int:
    # mapping simple (tu peux l’étendre ensuite)
    m = {
        "red": 0,
        "orange": 30,
        "yellow": 60,
        "green": 120,
        "cyan": 180,
        "blue": 240,
        "purple": 270,
        "pink": 300,
        "white": 0,
        "black": 0,
        "gray": 0,
        "brown": 30,
    }
    return int(m.get((color_name or "").lower(), 0))


def _bbox_head_from_person_bbox(person_bbox, head_ratio=0.20, expand=0.04):
    """
    Heuristique casque: on prend la zone haute de la bbox personne.
    person_bbox: dict vision agent {x,y,width,height} OU liste [x1,y1,x2,y2]
    Retour: [x1,y1,x2,y2]
    """
    if isinstance(person_bbox, dict):
        x1 = int(person_bbox["x"])
        y1 = int(person_bbox["y"])
        x2 = int(person_bbox["x"] + person_bbox["width"])
        y2 = int(person_bbox["y"] + person_bbox["height"])
    else:
        x1, y1, x2, y2 = map(int, person_bbox)

    w = x2 - x1
    h = y2 - y1

    head_h = int(h * head_ratio)

    hy1 = y1
    hy2 = y1 + head_h

    shrink = int(w * 0.06)
    hx1 = x1 + shrink
    hx2 = x2 - shrink

    ex = int((hx2 - hx1) * expand)
    ey = int((hy2 - hy1) * expand)

    hx1 = max(0, hx1 - ex)
    hy1 = max(0, hy1 - ey)
    hx2 = hx2 + ex
    hy2 = hy2 + ey

    return [int(hx1), int(hy1), int(hx2), int(hy2)]

#28janvier 
"""def _mask_to_png_b64(mask_uint8):
    
    #mask_uint8: (H,W) {0,1}
    #Retour: base64 PNG (grayscale)
    
    from PIL import Image
    buf = io.BytesIO()
    img = Image.fromarray((mask_uint8 * 255).astype(np.uint8), mode="L")
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")"""
import io, base64
import numpy as np
from PIL import Image

import io, base64
import numpy as np
from PIL import Image, ImageFilter

import io, base64
import numpy as np
from PIL import Image, ImageFilter

def _mask_to_png_b64_rgba(mask_uint8: np.ndarray, feather_radius: float = 3.0, dilate_px: int = 1) -> str:
    """
    mask_uint8: (H,W) {0,1} ou {0..255}
    feather_radius: flou gaussien sur l'alpha (2..6 recommandé)
    dilate_px: dilatation légère du masque avant blur (0..3)
    """
    m = mask_uint8.astype(np.uint8)
    if m.max() <= 1:
        m = m * 255

    # Alpha en image L
    alpha = Image.fromarray(m, mode="L")

    # (Optionnel) dilatation (agrandit un peu la sélection)
    # MaxFilter taille = 2*dilate+1
    if dilate_px > 0:
        alpha = alpha.filter(ImageFilter.MaxFilter(size=2 * dilate_px + 1))

    # Feather (bords doux)
    if feather_radius and feather_radius > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=float(feather_radius)))

    # Construire RGBA blanc avec alpha=masque feather
    w, h = alpha.size
    rgba = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    rgba.putalpha(alpha)

    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")



def _save_debug_masks(mask_uint8: np.ndarray, path_rgba="/tmp/debug_mask.png", path_l="/tmp/debug_mask_L.png"):
    """
    Debug visuel :
    - RGBA (alpha=mask) : ce que GIMP utilise
    - L (grayscale) : lecture humaine facile
    """
    m = mask_uint8.astype(np.uint8)
    if m.max() <= 1:
        m255 = m * 255
    else:
        m255 = m

    # L (lisible)
    Image.fromarray(m255, mode="L").save(path_l)

    # RGBA (alpha = mask)
    rgba = np.zeros((m255.shape[0], m255.shape[1], 4), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = m255
    Image.fromarray(rgba, mode="RGBA").save(path_rgba)


    # --- AFFICHAGE AUTOMATIQUE (Linux) ---
    print("[DEBUG] Masques générés :")
    print(" -", path_l)
    print(" -", path_rgba)

    try:
        import os
        os.system(f"xdg-open {path_l} >/dev/null 2>&1 &")
        os.system(f"xdg-open {path_rgba} >/dev/null 2>&1 &")
    except Exception as e:
        print("[DEBUG] Impossible d’ouvrir automatiquement les masques :", e)


def _segment_sam_bbox_point(image_rgb_uint8, bbox_xyxy):
    """
    image_rgb_uint8: np.ndarray (H,W,3) RGB
    bbox_xyxy: [x1,y1,x2,y2]
    retour: mask uint8 {0,1}
    """
    h, w, _ = image_rgb_uint8.shape
    x1, y1, x2, y2 = map(int, bbox_xyxy)

    # clamp bbox
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w - 1, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h - 1, y2))

    _sam_predictor.set_image(image_rgb_uint8)

    box = np.array([[x1, y1, x2, y2]], dtype=np.float32)

    cx = int((x1 + x2) / 2)
    cy = int((y1 + y2) / 2)
    point_coords = np.array([[cx, cy]], dtype=np.float32)
    point_labels = np.array([1], dtype=np.int32)

    masks, scores, _ = _sam_predictor.predict(
        box=box,
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=True
    )

    best_idx = int(np.argmax(scores))
    return masks[best_idx].astype(np.uint8)

def build_helmet_recolor_actions(image_path, target_color, image_b64):
    # 1) détecter personne
    vision_result = call_vision_agent(
        VISION_AGENT,
        image_b64,
        target="person",
        multi=False,
        max_instances=1,
        conf=0.25
    )
    inst = get_instances_from_vision(vision_result)[0]

    # 2) bbox casque (x1,y1,x2,y2)
    helmet_bbox = _bbox_head_from_person_bbox(inst["bbox"])
    x1, y1, x2, y2 = map(int, helmet_bbox)

    # 3) charger image
    with Image.open(image_path).convert("RGB") as im:
        image_rgb = np.array(im, dtype=np.uint8)

    H, W, _ = image_rgb.shape
    x1 = max(0, min(W - 1, x1))
    x2 = max(0, min(W,     x2))
    y1 = max(0, min(H - 1, y1))
    y2 = max(0, min(H,     y2))

    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"helmet_bbox invalide: {helmet_bbox}")

    # 4) SAM -> masque plein (H,W) {0,1}
    helmet_mask_full = _segment_sam_bbox_point(image_rgb, [x1, y1, x2, y2])

    # ✅ 5) FIX PROPRE : crop du masque à la bbox
    helmet_mask_roi = helmet_mask_full[y1:y2, x1:x2]

    # DEBUG VISUEL (ROI)
    _save_debug_masks(
        helmet_mask_roi,
        path_rgba="/tmp/debug_mask.png",
        path_l="/tmp/debug_mask_L.png"
    )
    print("[DEBUG] ROI mask saved to /tmp/debug_mask.png and /tmp/debug_mask_L.png")
    print("[DEBUG] ROI shape:", helmet_mask_roi.shape, "unique:", np.unique(helmet_mask_roi))

    # 6) PNG base64 RGBA (alpha=mask)
    mask_png_b64 = _mask_to_png_b64_rgba(helmet_mask_roi)
    print("[DEBUG] png_b64 length:", len(mask_png_b64))

    # 7) couleur
    hue = _color_to_hue(target_color)

    # 8) actions GIMP : offset = (x1,y1) car on envoie un ROI
    return [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask_png_b64,
                "offset_x": x1,
                "offset_y": y1
            },
            "notes": "Sélection casque (SAM ROI alpha)"
        },
        {
            "action": "apply_colorize_on_selection",
            "target": "selection",
            "params": {
                "hue": float(hue),
                "saturation": 100.0,
                "lightness": 0.0
            },
            "notes": "Colorize HSL sur sélection"
        },
        {
            "action": "clear_selection",
            "target": "selection",
            "params": {}
        }
    ]

        

    

def enrich_actions_with_chosen_instance(actions, inst):
    bbox = inst["bbox"]
    mask = inst["mask"]

    vision_actions = [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask["png_b64"],
                "offset_x": bbox["x"],
                "offset_y": bbox["y"]
            },
            "notes": f"Sélection instance {inst.get('id')}"
        },
       {
            "action": "delete_selection",
            "target": "image",
            "params": {},
            "notes": "Suppression pixels (Edit→Clear) sur le calque actif"
            },
            {
            "action": "clear_selection",
            "target": "selection",
            "params": {},
            "notes": "Select None"
            }


    ]

    cleaned = normalize_actions_for_gimp(actions)
    return vision_actions + cleaned


# ============================================================
# 🧠 PIPELINE PRINCIPAL
# ============================================================

print("🧠 SML + 👁️ Vision + 🎨 GIMP reliés ensemble !")

pending = None  # état de dialogue (ambiguïté)

while True:
    txt = input("🧑‍💻 Commande utilisateur > ").strip()

    if txt.lower() in ("quit", "exit", "q"):
        break

    # --------------------------------------------------------
    # 🔁 MODE CLARIFICATION
    # --------------------------------------------------------
    # --------------------------------------------------------
# 🔁 MODE CLARIFICATION (PLUSIEURS PERSONNES)
# --------------------------------------------------------
    if pending is not None:
        side = txt.lower()
        if side not in ("gauche", "droite"):
            print("Merci de répondre par 'gauche' ou 'droite'.")
            continue

        chosen = pick_instance_by_side(
            pending["instances"],
            side,
            pending["image_width"]
        )

        print(f"✅ Objet choisi : {side} (instance id={chosen.get('id')})")

        # 🔥 ICI LA MODIF IMPORTANTE
        actions = build_smart_inpaint_actions(chosen)
#14J
        print("➡️ Actions finales envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        # ok
        for i, a in enumerate(actions):
            if a.get("action") == "select_mask_png":
                params = a.get("params", {})
                b64 = params.get("png_b64", "")
                print(f"[CHECK] action[{i}] select_mask_png")
                print("        exists :", bool(b64))
                print("        length :", len(b64))

                if len(b64) < 500:
                    print("        ⚠️ WARNING: png_b64 too small → INVALID MASK")
                #ici 

        execute_actions(actions)

        pending = None
        print("\n" + "=" * 40 + "\n")
        continue

    # --------------------------------------------------------
    # 🧠 MODE NORMAL
    # --------------------------------------------------------
    analysis = semantic_analysis(txt)
    sml = call_sml(txt)
    actions = sml["actions"]
  # --------------------------------------------------------
    # 🎨 CAS : recolorer un casque (pipeline segmentation locale)
    # --------------------------------------------------------
    if (
        "colorize" in analysis.get("intents", [])
        and ("helmet" in analysis.get("objects", []) or "casque" in txt.lower())
    ):
        print("🪖 Pipeline CASQUE activé")

        # couleur cible (fallback)
        target_color = (analysis.get("colors") or ["red"])[0]

        actions = build_helmet_recolor_actions(
            image_path=IMAGE_PATH,
            target_color=target_color,
            image_b64=IMAGE_BASE64  # on réutilise ton image déjà encodée
        )

        print("➡️ Actions CASQUE envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))

        execute_actions(actions)
        print("\n" + "=" * 40 + "\n")
        continue


    print("🧠 Actions SML initiales :")
    print(json.dumps(actions, indent=2, ensure_ascii=False))
    # --------------------------------------------------------
# 🤖 CAS : supprimer une personne (SMART INPAINT)
# --------------------------------------------------------

    if (
        "remove_object" in analysis.get("intents", [])
        and "person" in analysis.get("objects", [])
    ):
        vision_result = call_vision_agent(
            VISION_AGENT,
            IMAGE_BASE64,
            target="person",
            multi=True,
            max_instances=5,
            conf=0.25
        )

        instances = get_instances_from_vision(vision_result)

        if len(instances) >= 2:
            pending = {
                "instances": instances,
                "image_width": IMAGE_WIDTH
            }
            print("J’ai détecté plusieurs personnes. Tu veux laquelle : gauche ou droite ?")
            continue

        actions = build_smart_inpaint_actions(instances[0])
        print("➡️ Actions SMART envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        execute_actions(actions)
        print("\n" + "=" * 40 + "\n")
        continue
