import json
import socket
import struct
import sys

cfg = json.loads(sys.stdin.buffer.read(4097))
assert cfg["host"] == "127.0.0.1"
actor = "python-child"
rows = []
for operation in ["tcp", "udp", "dns"]:
    sock = None
    row = {"actor": actor, "operation": operation, "ok": False, "code": None, "message": None, "timed_out": False}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM if operation == "tcp" else socket.SOCK_DGRAM)
        sock.settimeout(0.4)
        if operation == "dns":
            labels = (actor + ".qualification.invalid").split(".")
            question = b"".join(bytes([len(x)]) + x.encode("ascii") for x in labels) + b"\x00\x00\x01\x00\x01"
            payload = struct.pack("!6H", 0x1213, 0x0100, 1, 0, 0, 0) + question
            sock.sendto(payload, (cfg["host"], cfg["dns"]))
            response, _ = sock.recvfrom(512)
            row["ok"] = response[:2] == payload[:2] and response[-4:] == b"\x7f\x00\x00\x01"
        else:
            payload = ("atlas-1213:" + actor + ":" + operation).encode("ascii")
            if operation == "tcp":
                sock.connect((cfg["host"], cfg["tcp"]))
                sock.sendall(payload)
                response = b""
                while len(response) < len(payload):
                    response += sock.recv(128)
            else:
                sock.sendto(payload, (cfg["host"], cfg["udp"]))
                response, _ = sock.recvfrom(128)
            row["ok"] = response == payload
    except OSError as error:
        row.update(code=error.errno, message=str(error)[:512], timed_out=isinstance(error, TimeoutError))
    finally:
        if sock is not None:
            sock.close()
    rows.append(row)
print(json.dumps(rows))
