"""PDF text extraction: `pdftotext` when installed, else a stdlib reader.

The stdlib reader handles what these business PDFs use: ASCII85/FlateDecode content streams with
literal-string Tj/TJ text. It returns one line per BT..ET block in content order.
Anything else (encrypted, hex-encoded glyphs) raises, so coverage shows the failure.
"""
import base64
import re
import shutil
import subprocess
import zlib

STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
BLOCK = re.compile(rb"BT(.*?)ET", re.S)
TEXT_OP = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:\\.|[^\]])*\]\s*TJ", re.S)
LITERAL = re.compile(rb"\(((?:\\.|[^\\)])*)\)", re.S)
ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _unescape(raw):
    out, i = bytearray(), 0
    while i < len(raw):
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < len(raw):
            n = raw[i + 1:i + 2]
            if n.isdigit():
                j = i + 1
                while j < len(raw) and j < i + 4 and raw[j:j + 1].isdigit():
                    j += 1
                out.append(int(raw[i + 1:j], 8) & 255)
                i = j
                continue
            out += ESC.get(n, n)
            i += 2
            continue
        out += c
        i += 1
    return out.decode("cp1252", "replace")


def _stdlib_lines(path):
    data = path.read_bytes()
    lines = []
    for m in STREAM.finditer(data):
        raw = m.group(1).strip()
        try:
            if raw.endswith(b"~>"):  # ASCII85 wrapper, then Flate (ReportLab default)
                body = raw[2:-2] if raw.startswith(b"<~") else raw[:-2]
                raw = base64.a85decode(body)
            content = zlib.decompress(raw)
        except (zlib.error, ValueError):
            continue
        for b in BLOCK.finditer(content):
            parts = []
            for op in TEXT_OP.findall(b.group(1)):
                parts.append("".join(_unescape(x) for x in LITERAL.findall(op)))
            text = " ".join(p for p in parts if p).strip()
            if text:
                lines.append(text)
    if not lines:
        raise RuntimeError("no extractable text (unsupported PDF encoding)")
    return lines


def extract_lines(path):
    exe = shutil.which("pdftotext")
    if exe:
        r = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, text=True, check=True)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return _stdlib_lines(path)
