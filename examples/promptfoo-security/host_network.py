"""Owned loopback controls and actual denied Node/Python operations."""
import collections
import json
import os
import selectors
import socket
import struct
import threading
import time

ACTORS = ["node-parent", "node-child", "python-child"]
EXPECTED = {(actor, operation) for actor in ACTORS for operation in ["tcp", "udp", "dns"]}


def dns_reply(data):
    assert len(data) >= 17 and data[4:6] == b"\x00\x01"
    labels, offset = [], 12
    while data[offset]:
        count = data[offset]
        labels.append(data[offset + 1:offset + 1 + count].decode("ascii"))
        offset += count + 1
    assert labels[1:] == ["qualification", "invalid"] and labels[0] in ACTORS
    question = data[12:offset + 5]
    assert question[-4:] == b"\x00\x01\x00\x01"
    answer = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x00\x00\x04\x7f\x00\x00\x01"
    return labels[0], data[:2] + struct.pack("!5H", 0x8180, 1, 1, 0, 0) + question + answer


class Targets:
    def __init__(self):
        self.sockets, self.events, self.errors = {}, [], []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        for operation in ["tcp", "udp", "dns"]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM if operation == "tcp" else socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            if operation == "tcp":
                sock.listen(3)
            sock.setblocking(False)
            assert not os.get_inheritable(sock.fileno())
            self.sockets[operation] = sock

    def tcp(self, sock):
        conn, peer = sock.accept()
        with conn:
            conn.settimeout(0.5)
            data = b""
            while not data.endswith(b":tcp") and len(data) < 128:
                part = conn.recv(128)
                if not part:
                    break
                data += part
            actor = data.decode("ascii").split(":")[1]
            assert actor in ACTORS and data == ("atlas-1213:" + actor + ":tcp").encode()
            self.events.append({"actor": actor, "operation": "tcp", "peer": list(peer), "payload_hex": data.hex()})
            conn.sendall(data)

    def udp(self, sock, operation):
        data, peer = sock.recvfrom(512)
        if operation == "dns":
            actor, response = dns_reply(data)
        else:
            actor = data.decode("ascii").split(":")[1]
            assert actor in ACTORS and data == ("atlas-1213:" + actor + ":udp").encode()
            response = data
        self.events.append({"actor": actor, "operation": operation, "peer": list(peer), "payload_hex": data.hex()})
        sock.sendto(response, peer)

    def serve(self):
        selector = selectors.DefaultSelector()
        for name, sock in self.sockets.items():
            selector.register(sock, selectors.EVENT_READ, name)
        try:
            while not self.stop.is_set():
                for key, _ in selector.select(0.05):
                    if key.data == "tcp":
                        self.tcp(key.fileobj)
                    else:
                        self.udp(key.fileobj, key.data)
        except Exception as error:
            self.errors.append(repr(error))
        finally:
            selector.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        for sock in self.sockets.values():
            sock.close()
        if self.thread.is_alive():
            raise RuntimeError("owned target thread did not reap")


def positive(rows, events):
    assert len(rows) == 9 and {(row["actor"], row["operation"]) for row in rows} == EXPECTED
    assert all(row["ok"] and not row["timed_out"] for row in rows)
    assert collections.Counter((row["actor"], row["operation"]) for row in events) == collections.Counter(EXPECTED)


def negative(rows, before, events):
    assert len(rows) == 9 and {(row["actor"], row["operation"]) for row in rows} == EXPECTED
    assert all(not row["ok"] and row["code"] is not None and not row["timed_out"] for row in rows)
    assert all(row["code"] in ["EPERM", "EACCES", 1, 13] for row in rows if row["operation"] != "dns")
    assert before == events


def qualify(context, runtime):
    targets = Targets()
    cfg = {"host": "127.0.0.1", "python": str(context["paths"]["python"]),
           **{name: sock.getsockname()[1] for name, sock in targets.sockets.items()}}
    command = [str(context["paths"]["node"]), str(context["recipe"] / "network_probe.cjs"), "node-parent"]
    context.update(input=json.dumps(cfg).encode(), timeout=5)
    controls, denied, before, error = [], [], [], None
    targets.thread.start()
    try:
        controls = runtime.require_success(runtime.execute(command, context))
        time.sleep(0.1)
        positive(controls, targets.events)
        before = list(targets.events)
        denied = runtime.require_success(runtime.execute(context["prefix"] + command, context))
        time.sleep(0.1)
        negative(denied, before, targets.events)
        assert not targets.errors
    except Exception as failure:
        error = repr(failure)
    finally:
        targets.close()
    proof = {"classification": "NETWORK_CONTAINMENT_PASS" if error is None else "NETWORK_CONTAINMENT_FAILURE",
             "error": error, "targets": cfg, "positive_rows": controls, "denied_rows": denied,
             "positive_target_events": before, "all_target_events": targets.events, "server_errors": targets.errors,
             "target_thread_reaped": not targets.thread.is_alive(), "system_getaddrinfo_probe": "not exercised"}
    runtime.save(context["inputs"] / "network.json", proof)
    if error:
        raise RuntimeError(error)
    return proof
