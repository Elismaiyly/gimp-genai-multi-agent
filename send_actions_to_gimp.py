#!/usr/bin/env python3
import socket
import json

GIMP_HOST = "localhost"
GIMP_PORT = 9877


def send_to_gimp(payload: dict, timeout_sec: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_sec)
            s.connect((GIMP_HOST, GIMP_PORT))
            s.sendall(data)

            buffer = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buffer += chunk

        return json.loads(buffer.decode("utf-8"))

    except ConnectionRefusedError:
        return {"status": "error", "error": "Cannot connect to GIMP plugin (connection refused). Is GIMP running?"}
    except socket.timeout:
        return {"status": "error", "error": "Timeout waiting for GIMP response."}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def execute_actions(actions):
    payload = {"type": "execute_actions", "actions": actions}
    return send_to_gimp(payload)


if __name__ == "__main__":
    test_actions = [
        {"action": "apply_filter", "target": "image", "params": {"filter": "gaussian_blur", "radius": 8}}
    ]
    print(execute_actions(test_actions))
