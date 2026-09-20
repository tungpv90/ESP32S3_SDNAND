#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
upload_assets.py - day nguyen mot thu muc tu PC vao SD NAND MKDV1GCL-ABA
gan tren Seeed Studio XIAO ESP32-S3.

Chay chung voi sketch ESP32S3_SDNAND_Uploader.ino.

Vi du:
    python upload_assets.py
    python upload_assets.py --port COM7 --src "E:\\...\\assets" --dest /assets
    python upload_assets.py --clean          # xoa thu muc dich truoc khi nap
    python upload_assets.py --format         # format lai SD NAND roi nap
    python upload_assets.py --verify-only    # chi kiem tra, khong ghi

Giao thuc (text lenh + payload nhi phan, moi lenh tra ve dung 1 dong OK/ER):
    PING                          -> OK PONG <ver>
    INFO                          -> OK <mode> lfn=.. card=.. total=.. used=.. chunk=..
    STAT <path>                   -> OK <size> <crc32hex> | OK NONE
    PUT <size> <crc32hex> <path>  -> OK RDY, roi N chunk (moi chunk ack "K"),
                                     ket thuc bang OK DONE <crc32hex>
    RMDIR <path> / RM <path> / MKDIR <path> / LS <path> / FORMAT / REMOUNT
"""

import argparse
import os
import re
import sys
import time
import zlib

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("Thieu pyserial. Chay:  pip install pyserial")

CHUNK = 2048          # phai khop CHUNK_SIZE trong sketch
BAUD = 921600
ESPRESSIF_VID = 0x303A

DEFAULT_SRC = r"E:\Mochi\Dasai_Mochi\emoji_dansai_LoaAi.me\demo_jieli\assets"
DEFAULT_DEST = "/assets"


# --------------------------------------------------------------- tien ich
class LinkError(RuntimeError):
    pass


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def find_ports():
    """Uu tien cong cua chip Espressif / mo ta giong USB-serial."""
    scored = []
    for p in list_ports.comports():
        score = 0
        if (p.vid or 0) == ESPRESSIF_VID:
            score += 100
        desc = ((p.description or "") + " " + (p.manufacturer or "")).lower()
        for kw, pts in (("esp32", 60), ("jtag", 40), ("usb serial", 30),
                        ("ch340", 20), ("cp210", 20), ("silicon labs", 15)):
            if kw in desc:
                score += pts
        if p.device.upper() in ("COM1", "COM2"):
            score -= 200          # cong COM noi bo cua may, gan nhu chac chan khong phai
        scored.append((score, p.device, p.description or ""))
    scored.sort(reverse=True)
    return scored


# --------------------------------------------------------------- lop giao tiep
class Link:
    def __init__(self, port, baud=BAUD, debug=False):
        self.port = port
        self.debug = debug
        if "://" in port:
            # dung cho fake_device.py: socket://127.0.0.1:5555
            self.ser = serial.serial_for_url(port, baudrate=baud, timeout=1.0)
            return
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = 1.0
        self.ser.write_timeout = 20.0
        # Voi USB CDC cua ESP32-S3, DTR/RTS co the kich hoat reset -> tat di.
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # ---- doc/ghi tho
    def read_line(self, timeout=10.0):
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            if b == b"\n":
                return buf.decode("utf-8", "replace").strip()
            if b != b"\r":
                buf += b
        raise LinkError("timeout khi doc phan hoi (da nhan: %r)" % bytes(buf[:80]))

    # Log cua ESP-IDF co dang: "E (12345) sdmmc_periph: ..."
    _IDF_LOG = re.compile(r"^[EWIDV] \(\d+\) ")

    def read_reply(self, timeout=10.0):
        """Bo qua ghi chu '#' va log ESP-IDF, tra ve dong OK/ER dau tien."""
        deadline = time.time() + timeout
        while True:
            line = self.read_line(max(0.5, deadline - time.time()))
            if line.startswith("#") or line == "" or self._IDF_LOG.match(line):
                if self.debug:
                    print("   <", line)
                continue
            if self.debug:
                print("   <", line)
            return line

    def command(self, line, timeout=15.0):
        if self.debug:
            print("   >", line)
        self.ser.write((line + "\n").encode("utf-8"))
        self.ser.flush()
        reply = self.read_reply(timeout)
        if reply.startswith("ER"):
            raise LinkError(reply[3:].strip() or "loi khong ro")
        if not reply.startswith("OK"):
            raise LinkError("phan hoi la: %r" % reply)
        return reply[2:].strip()

    # ---- bat tay / dong bo lai
    def handshake(self, tries=12):
        for i in range(tries):
            try:
                self.ser.reset_input_buffer()
                self.ser.write(b"\nPING\n")
                self.ser.flush()
                reply = self.read_reply(1.5)
                if reply.startswith("OK PONG"):
                    return reply[8:].strip()
            except LinkError:
                pass
            if i == 3:
                # Mot so board chi xuat du lieu khi DTR duoc keo len
                try:
                    self.ser.dtr = True
                except Exception:
                    pass
            time.sleep(0.5)
        raise LinkError("khong thay firmware tra loi PING tren %s" % self.port)

    def resync(self):
        # Neu firmware dang ket trong readExact() cho not mot chunk, no chi
        # thoat sau 15s -> phai kien nhan lau hon khoang do.
        try:
            self.ser.reset_input_buffer()
            self.handshake(tries=14)
            return True
        except LinkError:
            return False

    # ---- lenh muc cao
    def info(self):
        raw = self.command("INFO")
        out = {"mode": raw.split(" ")[0]}
        for tok in raw.split(" ")[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k] = v
        return out

    def stat(self, path):
        """Tra ve (size, crc32) hoac None neu file chua co."""
        reply = self.command("STAT " + path, timeout=30.0)
        if reply == "NONE" or reply.startswith("NONE"):
            return None
        parts = reply.split()
        return int(parts[0]), int(parts[1], 16)

    def put(self, path, data):
        crc = zlib.crc32(data) & 0xFFFFFFFF
        reply = self.command("PUT %d %08x %s" % (len(data), crc, path), timeout=30.0)
        if not reply.startswith("RDY"):
            raise LinkError("cho RDY nhung nhan %r" % reply)
        sent = 0
        total = len(data)
        while sent < total:
            piece = data[sent:sent + CHUNK]
            self.ser.write(piece)
            self.ser.flush()
            ack = self.read_line(20.0)
            if ack != "K":
                if ack.startswith("ER"):
                    raise LinkError(ack[3:].strip())
                raise LinkError("ack la %r" % ack)
            sent += len(piece)
        done = self.read_reply(30.0)
        if not done.startswith("OK DONE"):
            raise LinkError(done)
        return crc


# --------------------------------------------------------------- luong chinh
def collect(src):
    """Liet ke file: [(duong dan tuyet doi, duong dan tuong doi kieu POSIX)]"""
    items = []
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src).replace("\\", "/")
            items.append((full, rel))
    return items


def main():
    ap = argparse.ArgumentParser(description="Nap thu muc vao SD NAND qua XIAO ESP32-S3")
    ap.add_argument("--port", help="vi du COM7 (mac dinh: tu do tim)")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--src", default=DEFAULT_SRC, help="thu muc nguon tren PC")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="thu muc dich tren the, vd /assets")
    ap.add_argument("--clean", action="store_true", help="xoa thu muc dich truoc khi nap")
    ap.add_argument("--format", action="store_true", help="format lai SD NAND truoc khi nap")
    ap.add_argument("--force", action="store_true", help="ghi de ca file da khop CRC")
    ap.add_argument("--verify-only", action="store_true", help="chi doi chieu CRC, khong ghi")
    ap.add_argument("--retries", type=int, default=3, help="so lan thu lai moi file")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        sys.exit("Khong thay thu muc nguon: %s" % src)
    dest = args.dest.replace("\\", "/")
    if ":" in dest:
        # Git Bash / MSYS bien "/assets" thanh "C:/Program Files/Git/assets".
        sys.exit("Duong dan dich khong hop le: %r\n"
                 "Neu ban chay tu Git Bash, dat MSYS_NO_PATHCONV=1 hoac dung"
                 " cmd/PowerShell (chay run_upload.bat)." % args.dest)
    dest = "/" + dest.strip("/") if dest.strip("/") else ""

    files = collect(src)
    total_bytes = sum(os.path.getsize(f) for f, _ in files)
    print("Nguon : %s" % src)
    print("Dich  : %s" % (dest or "/"))
    print("Gom   : %d file, %s" % (len(files), human(total_bytes)))

    # --- chon cong
    port = args.port
    if not port:
        cands = find_ports()
        if not cands:
            sys.exit("Khong thay cong COM nao. Cam board roi thu lai.")
        print("Cong  : cac ung vien -> " + ", ".join("%s (%s)" % (d, s) for _, d, s in cands))
        port = cands[0][1]
    print("Dung  : %s @ %d" % (port, args.baud))

    link = Link(port, args.baud, args.debug)
    try:
        ver = link.handshake()
        print("Firmware v%s da san sang." % ver)

        if args.format:
            print("Dang format SD NAND (co the mat vai giay)...")
            print("  -> " + link.command("FORMAT", timeout=120.0))

        nfo = link.info()
        print("The   : %s | LFN=%s | dung luong %s | da dung %s" % (
            nfo.get("mode"), nfo.get("lfn"),
            human(int(nfo.get("total", 0)) * 1024),
            human(int(nfo.get("used", 0)) * 1024)))
        if nfo.get("lfn") != "1":
            print("  !! FATFS khong bat Long File Name: ten kieu 'frame_000.png' se bi cat.")
            print("     Hay dung esp32 core >= 2.0.11 hoac doi ten file cho vua 8.3.")
        free = int(nfo.get("total", 0)) * 1024 - int(nfo.get("used", 0)) * 1024
        if not args.verify_only and free and free < total_bytes:
            print("  !! Dung luong trong (%s) it hon du lieu can nap (%s)."
                  % (human(free), human(total_bytes)))

        if args.clean and dest and not args.verify_only:
            print("Dang xoa %s tren the..." % dest)
            link.command("RMDIR " + dest, timeout=180.0)

        # --- nap
        sent = skipped = failed = 0
        sent_bytes = 0
        t0 = time.time()
        bad = []

        for idx, (full, rel) in enumerate(files, 1):
            remote = (dest + "/" + rel) if dest else ("/" + rel)
            with open(full, "rb") as fh:
                data = fh.read()
            crc = zlib.crc32(data) & 0xFFFFFFFF

            prefix = "[%4d/%d] %-34s %8s" % (idx, len(files), rel[-34:], human(len(data)))

            ok_already = False
            if not args.force:
                try:
                    st = link.stat(remote)
                    ok_already = bool(st) and st[0] == len(data) and st[1] == crc
                except LinkError as e:
                    if not link.resync():
                        raise
                    ok_already = False
                    if args.debug:
                        print("   (stat loi: %s)" % e)

            if ok_already:
                skipped += 1
                sys.stdout.write("\r%s  = da khop        " % prefix)
                sys.stdout.flush()
                continue

            if args.verify_only:
                failed += 1
                bad.append(rel)
                print("\r%s  X thieu/sai CRC   " % prefix)
                continue

            done = False
            for attempt in range(1, args.retries + 1):
                try:
                    link.put(remote, data)
                    done = True
                    break
                except (LinkError, serial.SerialException) as e:
                    sys.stdout.write("\r%s  ! lan %d: %s\n" % (prefix, attempt, e))
                    sys.stdout.flush()
                    link.resync()
                    time.sleep(0.3)
            if done:
                sent += 1
                sent_bytes += len(data)
                elapsed = max(0.001, time.time() - t0)
                sys.stdout.write("\r%s  > ok  %6.1f KB/s " % (prefix, sent_bytes / 1024.0 / elapsed))
                sys.stdout.flush()
            else:
                failed += 1
                bad.append(rel)
                print("\r%s  X THAT BAI        " % prefix)

        print()
        dt = time.time() - t0
        print("-" * 64)
        print("Da nap : %d file (%s) trong %.1fs" % (sent, human(sent_bytes), dt))
        print("Bo qua : %d file da khop CRC" % skipped)
        print("Loi    : %d file" % failed)
        if bad:
            print("Danh sach loi:")
            for b in bad[:30]:
                print("   " + b)
            if len(bad) > 30:
                print("   ... con %d file nua" % (len(bad) - 30))

        try:
            nfo = link.info()
            print("Con lai tren the: %s trong tong %s" % (
                human((int(nfo.get("total", 0)) - int(nfo.get("used", 0))) * 1024),
                human(int(nfo.get("total", 0)) * 1024)))
        except LinkError:
            pass

        link.command("DONE")
        return 1 if failed else 0
    finally:
        link.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\nDa huy.")
    except LinkError as e:
        sys.exit("\nLoi ket noi: %s" % e)
