#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
fake_device.py - gia lap firmware ESP32S3_SDNAND_Uploader qua TCP, de kiem thu
upload_assets.py ma khong can phan cung.

    python fake_device.py --root .\sandbox --port 5555
    python upload_assets.py --port socket://127.0.0.1:5555 --src <thu muc> --dest /assets

Logic o day bam sat sketch .ino: cung lenh, cung chunk 2048, cung ack "K".
"""

import argparse
import os
import socket
import zlib

CHUNK = 2048
FW = "1.0.0"


def recv_line(conn):
    buf = bytearray()
    while True:
        b = conn.recv(1)
        if not b:
            return None
        if b == b"\n":
            return buf.decode("utf-8", "replace").strip()
        if b != b"\r":
            buf += b


def recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        b = conn.recv(n - len(buf))
        if not b:
            break
        buf += b
    return bytes(buf)


class Device:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def local(self, path):
        rel = path.strip("/").replace("/", os.sep)
        return os.path.join(self.root, rel)

    def handle(self, conn, line):
        parts = line.split(" ", 1)
        cmd = parts[0].upper()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "PING":
            return "OK PONG " + FW
        if cmd == "INFO":
            used = sum(os.path.getsize(os.path.join(r, f))
                       for r, _, fs in os.walk(self.root) for f in fs)
            return ("OK FAKE lfn=1 card=131072 total=130048 used=%d chunk=%d fw=%s"
                    % (used // 1024, CHUNK, FW))
        if cmd == "STAT":
            p = self.local(arg)
            if not os.path.isfile(p):
                return "OK NONE"
            data = open(p, "rb").read()
            return "OK %d %08x" % (len(data), zlib.crc32(data) & 0xFFFFFFFF)
        if cmd == "MKDIR":
            os.makedirs(self.local(arg), exist_ok=True)
            return "OK"
        if cmd == "RM":
            try:
                os.remove(self.local(arg))
            except OSError:
                pass
            return "OK"
        if cmd == "RMDIR":
            import shutil
            shutil.rmtree(self.local(arg), ignore_errors=True)
            return "OK"
        if cmd == "FORMAT":
            import shutil
            shutil.rmtree(self.root, ignore_errors=True)
            os.makedirs(self.root, exist_ok=True)
            return "OK FORMATTED FAKE"
        if cmd == "LS":
            p = self.local(arg) if arg else self.root
            if not os.path.isdir(p):
                return "ER notdir"
            out = []
            for name in sorted(os.listdir(p)):
                f = os.path.join(p, name)
                out.append("%s %d %s" % ("D" if os.path.isdir(f) else "F",
                                         0 if os.path.isdir(f) else os.path.getsize(f), name))
            for o in out:
                conn.sendall((o + "\n").encode())
            return "OK END"
        if cmd == "DONE":
            return "OK BYE"
        if cmd == "PUT":
            return self.do_put(conn, arg)
        return "ER unknown " + cmd

    def do_put(self, conn, arg):
        try:
            size_s, crc_s, path = arg.split(" ", 2)
            size, want = int(size_s), int(crc_s, 16)
        except ValueError:
            return "ER syntax"
        p = self.local(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        conn.sendall(b"OK RDY\n")

        crc = 0
        left = size
        with open(p, "wb") as fh:
            while left:
                n = min(CHUNK, left)
                got = recv_exact(conn, n)
                if len(got) != n:
                    os.remove(p)
                    return "ER rxtimeout"
                fh.write(got)
                crc = zlib.crc32(got, crc)
                left -= n
                conn.sendall(b"K\n")
        crc &= 0xFFFFFFFF
        if crc != want:
            os.remove(p)
            return "ER crc %08x!=%08x" % (crc, want)
        return "OK DONE %08x" % crc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="sandbox_sdnand")
    ap.add_argument("--port", type=int, default=5555)
    args = ap.parse_args()

    dev = Device(args.root)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(1)
    print("fake SD NAND tai %s, nghe tren 127.0.0.1:%d" % (dev.root, args.port))

    while True:
        conn, _ = srv.accept()
        conn.sendall(b"\n# ESP32-S3 <-> MKDV1GCL-ABA SD NAND uploader v" + FW.encode() + b"\n")
        conn.sendall(b"# mounted: FAKE  card=128 MB  lfn=1\n# san sang, cho lenh tu PC...\n")
        try:
            while True:
                line = recv_line(conn)
                if line is None:
                    break
                if not line:
                    continue
                try:
                    reply = dev.handle(conn, line)
                except Exception as e:      # giong firmware: bao loi, khong chet
                    reply = "ER %s" % e
                conn.sendall((reply + "\n").encode())
        except (ConnectionError, OSError) as e:
            print("client ngat:", e)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
