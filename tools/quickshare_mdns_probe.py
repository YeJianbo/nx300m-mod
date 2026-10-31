"""Query the Nearby mDNS service on one explicit IPv4 interface (no port scan)."""
import argparse
import base64
import json
import socket
import struct
import time

SERVICE = "_FC9F5ED42C8A._tcp.local."


def name_at(packet, offset, seen=None):
    seen = set() if seen is None else seen
    labels = []
    while True:
        if offset in seen:
            raise ValueError("DNS compression cycle")
        seen.add(offset)
        size = packet[offset]
        offset += 1
        if size == 0:
            return ".".join(labels) + ".", offset
        if size & 0xC0 == 0xC0:
            pointer = ((size & 0x3F) << 8) | packet[offset]
            suffix, _ = name_at(packet, pointer, seen)
            return ".".join(labels + [suffix]), offset + 1
        labels.append(packet[offset:offset + size].decode("utf-8", "replace"))
        offset += size


def records(packet):
    _, _, questions, answers, authority, additional = struct.unpack_from("!6H", packet)
    pos = 12
    for _ in range(questions):
        _, pos = name_at(packet, pos)
        pos += 4
    for _ in range(answers + authority + additional):
        name, pos = name_at(packet, pos)
        kind, _, ttl, size = struct.unpack_from("!HHIH", packet, pos)
        pos += 10
        data = packet[pos:pos + size]
        record = {"name": name, "type": kind, "ttl": ttl}
        if kind == 12:
            record["target"], _ = name_at(packet, pos)
        elif kind == 33:
            record["port"] = struct.unpack_from("!HHH", packet, pos)[2]
            record["host"], _ = name_at(packet, pos + 6)
        elif kind == 1 and size == 4:
            record["ip"] = socket.inet_ntoa(data)
        elif kind == 16:
            txt = {}
            cursor = 0
            while cursor < size:
                length = data[cursor]
                item = data[cursor + 1:cursor + 1 + length].decode("utf-8", "replace")
                key, _, value = item.partition("=")
                txt[key] = value
                cursor += length + 1
            record["txt"] = txt
        yield record
        pos += size


def discover(interface, seconds):
    query = struct.pack("!6H", 0, 0, 1, 0, 0, 0)
    query += b"".join(bytes([len(p)]) + p.encode() for p in SERVICE.rstrip(".").split("."))
    query += b"\0" + struct.pack("!HH", 12, 0x8001)
    cache = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((interface, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        sock.settimeout(0.5)
        deadline, next_query = time.monotonic() + seconds, 0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_query:
                sock.sendto(query, ("224.0.0.251", 5353))
                next_query = time.monotonic() + 2
            try:
                packet, source = sock.recvfrom(65535)
            except socket.timeout:
                continue
            try:
                parsed = list(records(packet))
            except (ValueError, IndexError, struct.error):
                continue
            print(json.dumps({"event": "mdns_response", "source": source, "records": parsed}), flush=True)
            for record in parsed:
                cache[record["name"], record["type"]] = record
    for (_, kind), srv in cache.items():
        if kind != 33 or not srv["name"].lower().endswith(SERVICE.lower()) or not srv["ttl"]:
            continue
        addr = cache.get((srv["host"], 1))
        txt = cache.get((srv["name"], 16), {}).get("txt", {})
        if not addr or "n" not in txt:
            continue
        raw = base64.urlsafe_b64decode(txt["n"] + "=" * (-len(txt["n"]) % 4))
        label = raw[18:18 + raw[17]].decode("utf-8", "replace") if len(raw) > 17 else ""
        yield {"name": label or srv["host"].rstrip("."), "ip": addr["ip"], "port": srv["port"],
               "endpoint_info_bytes": len(raw), "fullname": srv["name"], "present": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    for endpoint in discover(args.interface, args.seconds):
        print(json.dumps({"event": "discovered", "endpoint": endpoint}), flush=True)
