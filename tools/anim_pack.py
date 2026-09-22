#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anim_pack.py - dong goi thu muc assets/ thanh bo file .ANM/.AWV/ANIM.MAN
roi day vao SD NAND qua XIAO ESP32-S3.

Luong:
    assets/anim.json + assets/<folder>/*.png + dung 1 file audio
        -> build/ANIM/<NAME>.ANM   (header + frame RGB565 lien tiep)
        -> build/ANIM/<NAME>.AWV   (RIFF/WAVE MS-ADPCM, mono)
        -> build/ANIM/ANIM.MAN     (bang tra: cu chi -> file .ANM)
        -> /ANIM tren the

Vi du:
    python tools/anim_pack.py init                  # do assets/ -> sinh anim.json
    python tools/anim_pack.py build                 # dong goi ra build/ANIM
    python tools/anim_pack.py build --codec rle     # nen RLE cho nhe
    python tools/anim_pack.py upload --port COM7    # day build/ANIM vao /ANIM
    python tools/anim_pack.py info build/ANIM/BATNGO.ANM
"""

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import wave
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SRC = os.path.join(HERE, "assets")
DEFAULT_OUT = os.path.join(ROOT, "build", "ANIM")
DEFAULT_DEST = "/ANIM"

IMAGE_EXT = (".png", ".bmp", ".jpg", ".jpeg", ".gif")
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac")

ANM_MAGIC = b"ANM1"
ANM_HEADER = 40

PIXFMT_RGB565_LE = 0
PIXFMT_RGB565_BE = 1
PIXFMT_RGB565_LE_RLE = 2
PIXFMT_RGB565_BE_RLE = 3


class PackError(RuntimeError):
    pass


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


# --------------------------------------------------------------- sap xep tu nhien
_NUM = re.compile(r"(\d+)")


def natural_key(name):
    """frame_2.png dung truoc frame_10.png."""
    return [int(t) if t.isdigit() else t.lower() for t in _NUM.split(name)]


def safe_name(text, taken=None):
    """Bop ve ten 8.3 an toan cho FAT: <= 8 ky tu, A-Z 0-9."""
    s = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    if not s:
        s = "ANIM"
    if s[0].isdigit():
        s = "A" + s
    s = s[:8]
    if taken is None:
        return s
    base = s
    i = 1
    while s in taken:
        suffix = str(i)
        s = base[:8 - len(suffix)] + suffix
        i += 1
    taken.add(s)
    return s


def parse_color(text):
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        raise PackError("mau nen phai dang RRGGBB, vd 000000")
    return (int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16))


# --------------------------------------------------------------- anh -> RGB565
def load_frame(path, width, height, bg):
    """Doc 1 anh -> list pixel RGB565 (int), da ghep alpha len nen bg."""
    try:
        from PIL import Image
    except ImportError:
        raise PackError("Thieu Pillow. Chay:  pip install pillow")

    with Image.open(path) as im:
        im = im.convert("RGBA")
        if im.size != (width, height):
            im = im.resize((width, height), Image.LANCZOS)
        flat = Image.new("RGBA", im.size, (bg[0], bg[1], bg[2], 255))
        flat.alpha_composite(im)
        data = flat.convert("RGB").tobytes()

    out = []
    for i in range(0, len(data), 3):
        out.append(((data[i] & 0xF8) << 8)
                   | ((data[i + 1] & 0xFC) << 3)
                   | (data[i + 2] >> 3))
    return out


def pack_raw(pixels, big_endian):
    return struct.pack((">%dH" if big_endian else "<%dH") % len(pixels), *pixels)


def pack_rle(pixels, big_endian):
    """Token u8 n: n&0x80 -> (n&0x7F)+1 pixel doc thang, nguoc lai lap n+1 lan."""
    one = ">H" if big_endian else "<H"
    many = ">%dH" if big_endian else "<%dH"
    out = bytearray()
    i = 0
    total = len(pixels)
    while i < total:
        run = 1
        while run < 128 and i + run < total and pixels[i + run] == pixels[i]:
            run += 1
        if run >= 3 or (run == 2 and i + 2 >= total):
            out.append(run - 1)
            out += struct.pack(one, pixels[i])
            i += run
            continue
        start = i
        while (i - start) < 128 and i < total:
            # dung khoi literal lai khi sap gap chuoi lap >= 3
            if (i + 2 < total and pixels[i + 1] == pixels[i]
                    and pixels[i + 2] == pixels[i] and i > start):
                break
            i += 1
        n = i - start
        out.append(0x80 | (n - 1))
        out += struct.pack(many % n, *pixels[start:i])
    return bytes(out)


def unpack_rle(blob, npixels, big_endian):
    """Giai nen (dung cho self-test)."""
    one = ">H" if big_endian else "<H"
    out = []
    i = 0
    while len(out) < npixels:
        tok = blob[i]
        i += 1
        if tok & 0x80:
            n = (tok & 0x7F) + 1
            out += list(struct.unpack((">%dH" if big_endian else "<%dH") % n,
                                      blob[i:i + n * 2]))
            i += n * 2
        else:
            n = tok + 1
            out += [struct.unpack(one, blob[i:i + 2])[0]] * n
            i += 2
    return out


def build_anm(folder, frames, width, height, fps, loop, codec, big_endian, bg, log):
    """Sinh noi dung file .ANM."""
    body = bytearray()
    index = []
    rle = codec == "rle"

    for n, fn in enumerate(frames, 1):
        pixels = load_frame(os.path.join(folder, fn), width, height, bg)
        blob = pack_rle(pixels, big_endian) if rle else pack_raw(pixels, big_endian)
        index.append((len(body), len(blob)))
        body += blob
        if log and (n % 25 == 0 or n == len(frames)):
            sys.stdout.write("\r      ... frame %d/%d" % (n, len(frames)))
            sys.stdout.flush()
    if log:
        sys.stdout.write("\r" + " " * 32 + "\r")

    if rle:
        pixfmt = PIXFMT_RGB565_BE_RLE if big_endian else PIXFMT_RGB565_LE_RLE
        table = b"".join(struct.pack("<II", off, ln) for off, ln in index)
        frame_bytes = 0
        index_offset = ANM_HEADER
        data_offset = ANM_HEADER + len(table)
        payload = table + bytes(body)
    else:
        pixfmt = PIXFMT_RGB565_BE if big_endian else PIXFMT_RGB565_LE
        frame_bytes = width * height * 2
        index_offset = 0
        data_offset = ANM_HEADER
        payload = bytes(body)

    header = struct.pack(
        "<4sHHHHBBHIIIIII",
        ANM_MAGIC, width, height, len(frames), fps,
        pixfmt, 1 if loop else 0, ANM_HEADER,
        frame_bytes, index_offset, data_offset, len(payload),
        zlib.crc32(payload) & 0xFFFFFFFF, 0)
    if len(header) != ANM_HEADER:
        raise PackError("header ANM dai %d byte, cho %d" % (len(header), ANM_HEADER))
    return header + payload


def read_anm_header(blob):
    if len(blob) < ANM_HEADER or blob[:4] != ANM_MAGIC:
        raise PackError("khong phai file ANM")
    (_, w, h, nframes, fps, pixfmt, flags, hdr,
     frame_bytes, index_off, data_off, data_len, crc, _) = struct.unpack(
        "<4sHHHHBBHIIIIII", blob[:ANM_HEADER])
    return dict(width=w, height=h, frames=nframes, fps=fps, pixfmt=pixfmt,
                loop=bool(flags & 1), header_size=hdr, frame_bytes=frame_bytes,
                index_offset=index_off, data_offset=data_off,
                data_bytes=data_len, crc32=crc)


# --------------------------------------------------------------- audio -> MS-ADPCM
ADAPT_TABLE = (230, 230, 230, 230, 307, 409, 512, 614,
               768, 614, 512, 409, 307, 230, 230, 230)
ADAPT_COEF1 = (256, 512, 0, 192, 240, 460, 392)
ADAPT_COEF2 = (0, -256, 0, 64, 0, -208, -232)


def read_wav_mono(path, target_rate=None):
    """Doc WAV PCM -> (list mau int16 mono, sample rate)."""
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if width == 2:
        n = len(raw) // 2
        samples = list(struct.unpack("<%dh" % n, raw[:n * 2]))
    elif width == 1:
        samples = [(b - 128) << 8 for b in raw]
    else:
        raise PackError("WAV %d-bit chua ho tro (chi 8/16-bit PCM): %s"
                        % (width * 8, os.path.basename(path)))

    if channels > 1:
        mono = []
        for i in range(0, len(samples) - channels + 1, channels):
            mono.append(sum(samples[i:i + channels]) // channels)
        samples = mono

    if target_rate and target_rate != rate and len(samples) > 1:
        n_out = max(1, int(len(samples) * target_rate / float(rate)))
        step = (len(samples) - 1) / float(n_out) if n_out > 1 else 0.0
        out = []
        for i in range(n_out):
            pos = i * step
            j = int(pos)
            frac = pos - j
            a = samples[j]
            b = samples[j + 1] if j + 1 < len(samples) else a
            out.append(int(a + (b - a) * frac))
        samples = out
        rate = target_rate

    return samples, rate


def decode_with_ffmpeg(path, target_rate):
    """Nguon khong phai WAV PCM -> nho ffmpeg giai ra PCM 16-bit mono."""
    exe = None
    for cand in ("ffmpeg", "ffmpeg.exe"):
        try:
            subprocess.run([cand, "-version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
            exe = cand
            break
        except (OSError, subprocess.CalledProcessError):
            continue
    if not exe:
        raise PackError("khong doc duoc %s bang Python va may khong co ffmpeg.\n"
                        "      Hay chuyen file audio sang WAV PCM 16-bit truoc."
                        % os.path.basename(path))
    cmd = [exe, "-v", "error", "-i", path, "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1"]
    if target_rate:
        cmd += ["-ar", str(target_rate)]
    cmd += ["-"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise PackError("ffmpeg loi voi %s: %s"
                        % (os.path.basename(path),
                           res.stderr.decode("utf-8", "replace").strip()[:200]))
    pcm = res.stdout
    n = len(pcm) // 2
    return list(struct.unpack("<%dh" % n, pcm[:n * 2])), target_rate


def _clamp16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def _initial_deltas(samples):
    """Vai ung vien delta khoi dau, bam theo bien do dau block."""
    spread = 0
    for i in range(2, min(len(samples), 34)):
        d = abs(samples[i] - samples[i - 1])
        if d > spread:
            spread = d
    cands = []
    for scale in (1.0, 0.5, 0.25, 2.0):
        v = max(16, min(int(spread * scale), 16384))
        if v not in cands:
            cands.append(v)
    return cands


def _encode_block(samples, predictor, delta):
    """Ma hoa 1 block MS-ADPCM mono -> (bytes, tong sai so binh phuong)."""
    coef1 = ADAPT_COEF1[predictor]
    coef2 = ADAPT_COEF2[predictor]

    s2 = samples[0]
    s1 = samples[1]

    out = bytearray()
    out.append(predictor)
    out += struct.pack("<hhh", delta, s1, s2)

    err = 0
    nibbles = []
    for i in range(2, len(samples)):
        target = samples[i]
        pred = (s1 * coef1 + s2 * coef2) >> 8
        code = int(round((target - pred) / float(delta)))
        if code > 7:
            code = 7
        elif code < -8:
            code = -8
        recon = _clamp16(pred + code * delta)
        e = recon - target
        err += e * e
        nib = code & 0x0F
        nibbles.append(nib)
        delta = (ADAPT_TABLE[nib] * delta) >> 8
        if delta < 16:
            delta = 16
        s2 = s1
        s1 = recon

    for i in range(0, len(nibbles) - 1, 2):
        out.append((nibbles[i] << 4) | nibbles[i + 1])
    if len(nibbles) % 2:
        out.append(nibbles[-1] << 4)
    return bytes(out), err


def encode_ms_adpcm(samples, block_align=256, search=True):
    """-> (du lieu ADPCM, so mau moi block, tong so mau da ma hoa)."""
    if block_align < 16 or block_align % 2:
        raise PackError("block align phai chan va >= 16")
    per_block = (block_align - 7) * 2 + 2
    blocks = bytearray()
    total = 0
    for start in range(0, len(samples), per_block):
        chunk = samples[start:start + per_block]
        if len(chunk) < 2:
            break
        if len(chunk) < per_block:
            chunk = chunk + [chunk[-1]] * (per_block - len(chunk))
        deltas = _initial_deltas(chunk)
        if search:
            best = None
            for p in range(7):
                for d in deltas:
                    blob, err = _encode_block(chunk, p, d)
                    if best is None or err < best[1]:
                        best = (blob, err)
            blob = best[0]
        else:
            blob, _ = _encode_block(chunk, 0, deltas[0])
        blocks += blob.ljust(block_align, b"\x00")[:block_align]
        total += per_block
    return bytes(blocks), per_block, total


def build_awv(src, rate_override, block_align, search):
    """Sinh noi dung file .AWV (RIFF/WAVE MS-ADPCM mono)."""
    ext = os.path.splitext(src)[1].lower()
    if ext == ".wav":
        try:
            samples, rate = read_wav_mono(src, rate_override)
        except (wave.Error, EOFError, PackError):
            samples, rate = decode_with_ffmpeg(src, rate_override or 16000)
    else:
        samples, rate = decode_with_ffmpeg(src, rate_override or 16000)

    if len(samples) < 2:
        raise PackError("khong co mau nao trong %s" % os.path.basename(src))

    data, per_block, nsamples = encode_ms_adpcm(samples, block_align, search)

    coefs = []
    for a, b in zip(ADAPT_COEF1, ADAPT_COEF2):
        coefs += [a & 0xFFFF, b & 0xFFFF]
    fmt = struct.pack("<HHIIHHHHH" + "H" * 14,
                      0x0002,                               # wFormatTag MS-ADPCM
                      1,                                    # mono
                      rate,
                      (rate * block_align) // per_block,     # byte rate xap xi
                      block_align,
                      4,                                    # bit moi mau
                      32,                                   # cbSize
                      per_block,                            # wSamplesPerBlock
                      7,                                    # wNumCoef
                      *coefs)
    fact = struct.pack("<I", nsamples)
    body = (b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"fact" + struct.pack("<I", len(fact)) + fact
            + b"data" + struct.pack("<I", len(data)) + data
            + (b"\x00" if len(data) % 2 else b""))
    return b"RIFF" + struct.pack("<I", len(body)) + body, rate, nsamples


# --------------------------------------------------------------- du an
def scan_folders(src):
    out = []
    for name in sorted(os.listdir(src)):
        full = os.path.join(src, name)
        if not os.path.isdir(full):
            continue
        entries = os.listdir(full)
        imgs = [f for f in entries if f.lower().endswith(IMAGE_EXT)]
        auds = [f for f in entries if f.lower().endswith(AUDIO_EXT)]
        if imgs:
            out.append((name, sorted(imgs, key=natural_key), sorted(auds)))
    return out


def cmd_init(args):
    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        raise PackError("khong thay thu muc nguon: %s" % src)
    folders = scan_folders(src)
    if not folders:
        raise PackError("khong thay thu muc con nao co anh trong %s" % src)

    width, height = args.width, args.height
    try:
        from PIL import Image
        with Image.open(os.path.join(src, folders[0][0], folders[0][1][0])) as im:
            width, height = im.size
    except Exception:
        pass

    taken = set()
    anims = []
    for folder, imgs, auds in folders:
        name = safe_name(folder, taken)
        anims.append({"gesture": name, "name": name, "folder": folder,
                      "fps": args.fps, "loop": True})
        print("  %-12s -> %-8s %3d frame, audio: %s"
              % (folder, name, len(imgs), auds[0] if auds else "(khong co)"))

    path = os.path.join(src, "anim.json")
    if os.path.exists(path) and not args.force:
        raise PackError("%s da ton tai. Them --force de ghi de." % path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"width": width, "height": height, "animations": anims},
                  fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("\nDa ghi %s (%d animation, %dx%d)." % (path, len(anims), width, height))
    print('Sua truong "gesture" trong file do de gan cu chi that '
          '(STILL / MOVE / SHAKE / TILT...).')
    return 0


def load_project(src):
    path = os.path.join(src, "anim.json")
    if not os.path.isfile(path):
        raise PackError("khong thay %s.\n      Chay:  python tools/anim_pack.py init" % path)
    with open(path, "r", encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    if "animations" not in doc:
        raise PackError('anim.json thieu truong "animations"')
    return doc


def cmd_build(args):
    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    doc = load_project(src)
    width = int(doc.get("width", args.width))
    height = int(doc.get("height", args.height))
    bg = parse_color(args.bg)
    big_endian = args.byte_order == "be"

    os.makedirs(out, exist_ok=True)
    print("Nguon : %s" % src)
    print("Dich  : %s" % out)
    print("Khung : %dx%d, RGB565 %s, codec %s"
          % (width, height, args.byte_order.upper(), args.codec))
    print("-" * 68)

    taken = set()
    rows = []
    total_bytes = 0
    for spec in doc["animations"]:
        folder_rel = spec.get("folder") or spec.get("name")
        if not folder_rel:
            raise PackError('mot muc trong anim.json thieu ca "folder" lan "name"')
        folder = os.path.join(src, folder_rel)
        if not os.path.isdir(folder):
            raise PackError("khong thay thu muc %s (khai bao trong anim.json)" % folder)

        wanted = spec.get("name") or folder_rel
        name = safe_name(wanted, taken)
        if name != wanted:
            print('  !! ten "%s" khong hop 8.3 -> dung "%s"' % (wanted, name))
        gesture = (spec.get("gesture") or name).upper()
        fps = int(spec.get("fps") or args.fps)
        loop = bool(spec.get("loop", True))

        entries = os.listdir(folder)
        imgs = sorted([f for f in entries if f.lower().endswith(IMAGE_EXT)], key=natural_key)
        auds = sorted([f for f in entries if f.lower().endswith(AUDIO_EXT)])
        if not imgs:
            raise PackError("thu muc %s khong co anh nao" % folder_rel)
        if len(auds) > 1:
            raise PackError("thu muc %s co %d file audio (%s) - chi duoc dung 1"
                            % (folder_rel, len(auds), ", ".join(auds)))

        print("  %-8s <- %-12s %3d frame @ %2d fps  %s"
              % (name, folder_rel, len(imgs), fps, "loop" if loop else "once"))

        blob = build_anm(folder, imgs, width, height, fps, loop, args.codec,
                         big_endian, bg, not args.quiet and sys.stdout.isatty())
        anm_name = name + ".ANM"
        with open(os.path.join(out, anm_name), "wb") as fh:
            fh.write(blob)
        total_bytes += len(blob)
        line = "      %-12s %9s" % (anm_name, human(len(blob)))
        if args.codec == "rle":
            raw_size = len(imgs) * width * height * 2
            line += "   %.0f%% so voi raw" % (100.0 * len(blob) / raw_size)
        print(line)

        awv_name = "-"
        if auds:
            data, rate, nsamples = build_awv(os.path.join(folder, auds[0]),
                                             args.audio_rate, args.block_align,
                                             not args.fast_audio)
            awv_name = name + ".AWV"
            with open(os.path.join(out, awv_name), "wb") as fh:
                fh.write(data)
            total_bytes += len(data)
            print("      %-12s %9s   MS-ADPCM %d Hz, %.1fs  <- %s"
                  % (awv_name, human(len(data)), rate, nsamples / float(rate), auds[0]))

        frame_bytes = 0 if args.codec == "rle" else width * height * 2
        rows.append((gesture, name, anm_name, awv_name, fps,
                     1 if loop else 0, len(imgs), frame_bytes))

    man = ["# ANIM.MAN v1 - bang tra cu chi -> animation",
           "# MAN1,<width>,<height>,<so animation>",
           "# <gesture>,<name>,<anm>,<awv|->,<fps>,<loop>,<frames>,<frame_bytes>",
           "MAN1,%d,%d,%d" % (width, height, len(rows))]
    for row in rows:
        man.append(",".join(str(v) for v in row))
    man_blob = ("\n".join(man) + "\n").encode("ascii")
    with open(os.path.join(out, "ANIM.MAN"), "wb") as fh:
        fh.write(man_blob)
    total_bytes += len(man_blob)

    seen = {}
    for row in rows:
        seen.setdefault(row[0], []).append(row[1])
    dup = dict((g, n) for g, n in seen.items() if len(n) > 1)

    print("-" * 68)
    print("ANIM.MAN: %d muc | tong bo dong goi: %s" % (len(rows), human(total_bytes)))
    if dup:
        print("  !! cu chi bi trung, firmware se lay muc dau tien:")
        for g in sorted(dup):
            print("     %-10s -> %s" % (g, ", ".join(dup[g])))
    print("")
    print("Day len the:  python tools/anim_pack.py upload --port COMx")
    return 0


def cmd_upload(args):
    out = os.path.abspath(args.out)
    if not os.path.isdir(out):
        raise PackError("chua co %s. Chay build truoc." % out)
    dest = args.dest.replace("\\", "/")
    if ":" in dest:
        raise PackError("duong dan dich khong hop le: %r\n"
                        "      Neu chay tu Git Bash, dat MSYS_NO_PATHCONV=1 hoac"
                        " dung cmd/PowerShell." % args.dest)
    cmd = [sys.executable, os.path.join(HERE, "upload_assets.py"),
           "--src", out, "--dest", dest]
    if args.port:
        cmd += ["--port", args.port]
    for flag in ("clean", "force", "debug"):
        if getattr(args, flag):
            cmd.append("--" + flag)
    if args.verify_only:
        cmd.append("--verify-only")
    elif args.format:
        # Mac dinh: xoa sach the truoc moi lan nap. Tat bang --no-format.
        cmd.append("--format")
        print("!! FORMAT: toan bo SD NAND se bi xoa truoc khi nap"
              " (bo qua bang --no-format)")
    print("> " + " ".join(cmd))
    sys.stdout.flush()
    return subprocess.call(cmd)


def cmd_info(args):
    for path in args.files:
        print("=== %s  (%s)" % (path, human(os.path.getsize(path))))
        with open(path, "rb") as fh:
            blob = fh.read()
        if blob[:4] == ANM_MAGIC:
            h = read_anm_header(blob)
            for k in ("width", "height", "frames", "fps", "pixfmt", "loop",
                      "frame_bytes", "index_offset", "data_offset", "data_bytes"):
                print("   %-13s %s" % (k, h[k]))
            crc = zlib.crc32(blob[h["index_offset"] or h["data_offset"]:]) & 0xFFFFFFFF
            print("   %-13s %08x (%s)"
                  % ("crc32", h["crc32"], "khop" if crc == h["crc32"] else "SAI"))
            print("   %-13s %.1fs" % ("thoi luong", h["frames"] / float(h["fps"] or 1)))
        elif blob[:4] == b"RIFF":
            i = blob.find(b"fmt ")
            tag, ch, rate, _brate, align, bits = struct.unpack("<HHIIHH", blob[i + 8:i + 24])
            print("   %-13s %d (%s)" % ("format", tag, "MS-ADPCM" if tag == 2 else "khac"))
            print("   %-13s %d" % ("channels", ch))
            print("   %-13s %d Hz" % ("rate", rate))
            print("   %-13s %d bit" % ("bits", bits))
            print("   %-13s %d" % ("block align", align))
            if tag == 2:
                print("   %-13s %d" % ("samples/block",
                                       struct.unpack("<H", blob[i + 26:i + 28])[0]))
            j = blob.find(b"fact")
            if j > 0:
                n = struct.unpack("<I", blob[j + 8:j + 12])[0]
                print("   %-13s %d (%.2fs)" % ("samples", n, n / float(rate)))
        else:
            sys.stdout.write(blob.decode("ascii", "replace"))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Dong goi assets/ thanh .ANM/.AWV/ANIM.MAN va day vao SD NAND")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="do assets/ roi sinh anim.json")
    p.add_argument("--src", default=DEFAULT_SRC)
    p.add_argument("--fps", type=int, default=10)
    p.add_argument("--width", type=int, default=96)
    p.add_argument("--height", type=int, default=64)
    p.add_argument("--force", action="store_true", help="ghi de anim.json dang co")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build", help="dong goi ra build/ANIM")
    p.add_argument("--src", default=DEFAULT_SRC)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--codec", choices=("raw", "rle"), default="raw",
                   help="raw = frame RGB565 lien tiep (mac dinh); rle = nen lai")
    p.add_argument("--byte-order", choices=("le", "be"), default="le",
                   help="thu tu byte RGB565 (mac dinh le, hop voi ESP32)")
    p.add_argument("--bg", default="000000", help="mau nen de ghep alpha, vd 000000")
    p.add_argument("--fps", type=int, default=10, help="fps mac dinh khi anim.json thieu")
    p.add_argument("--width", type=int, default=96)
    p.add_argument("--height", type=int, default=64)
    p.add_argument("--audio-rate", type=int, default=None,
                   help="ep sample rate, vd 16000 (mac dinh giu nguyen)")
    p.add_argument("--block-align", type=int, default=256, help="block MS-ADPCM")
    p.add_argument("--fast-audio", action="store_true",
                   help="bo qua viec do 7 bo he so: nhanh hon, chat luong hoi kem")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("upload", help="day build/ANIM vao the qua upload_assets.py")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--dest", default=DEFAULT_DEST)
    p.add_argument("--port")
    p.add_argument("--no-format", dest="format", action="store_false", default=True,
                   help="dung format the truoc khi nap (mac dinh la co format)")
    p.add_argument("--clean", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("info", help="doc header cua .ANM / .AWV / ANIM.MAN")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_info)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackError as e:
        sys.exit("\nLoi: %s" % e)
    except KeyboardInterrupt:
        sys.exit("\nDa huy.")
