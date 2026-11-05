"""Read portable Anki cards and convert their content to safe study HTML."""

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import struct
import time
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from zipfile import BadZipFile, ZipFile

try:
    from compression import zstd
except ImportError:
    zstd = None

ZSTD_ERRORS = (zstd.ZstdError,) if zstd is not None else ()

MIB = 1024 ** 2
UPLOAD_LIMIT = 64 * MIB
MAX_CARDS = 5000
MAX_PACKAGE_FILES = 100_000
MAX_PACKAGE_DIRECTORY = 16 * MIB
MAX_PACKAGE_CARDS = 75_000
MAX_PACKAGE_CARD_TEXT = 64 * MIB
TAGS = set("b strong em i u s del sub sup mark div span p br hr ul ol li blockquote pre code "
           "table thead tbody tr td th ruby rt rp".split())
BLOCKS = set("div p br hr li blockquote pre tr".split())
DROP = {"script", "style", "iframe", "object", "svg", "math", "template", "canvas"}
MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
}
CLOZE = re.compile(r"{{c([0-9]+)::(.*?)(?:::(.*?))?}}", re.S)
TOKEN = re.compile(r"{{([^{}]+)}}")
SOUND = re.compile(r"\[sound:([^\]\n]+)\]")


class DeckImportError(ValueError):
    pass


@dataclass
class Deck:
    cards: list = field(default_factory=list)
    media: dict = field(default_factory=dict)
    skipped: int = 0
    missing: set = field(default_factory=set)


class CardHTML(HTMLParser):
    def __init__(self, asset=None):
        super().__init__(convert_charrefs=True)
        self.asset = asset
        self.parts = []
        self.text = []
        self.blocked = []
        self.has_media = False

    def handle_starttag(self, tag, attrs):
        if tag in DROP:
            self.blocked.append(tag)
        if self.blocked:
            return
        if tag in TAGS:
            self.parts.append(f"<{tag}>")
        if tag in BLOCKS:
            self.text.append("\n")
        if self.asset and tag in ("img", "audio"):
            values = dict(attrs)
            source = values.get("src")
            uri = self.asset(source, tag) if source else None
            if uri:
                self.has_media = True
                if tag == "img":
                    alt = escape(values.get("alt") or "", quote=True)
                    self.parts.append(f'<img src="{uri}" alt="{alt}" loading="lazy">')
                else:
                    self.parts.append(f'<audio controls preload="none" src="{uri}"></audio>')
            else:
                self.handle_data("[Media unavailable]")

    def handle_endtag(self, tag):
        if self.blocked:
            if self.blocked[-1] == tag:
                self.blocked.pop()
            return
        if tag in TAGS - {"br", "hr"}:
            self.parts.append(f"</{tag}>")
        if tag in BLOCKS:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data))
            self.text.append(data)


def plain_text(source):
    parser = CardHTML()
    parser.feed(source)
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip())


def clean_html(source, asset):
    parser = CardHTML(asset)
    parser.feed(SOUND.sub(lambda match: '<audio src="' + escape(match[1], quote=True) + '"></audio>', source))
    parser.close()
    if not parser.has_media and not "".join(parser.text).strip():
        return ""
    return "".join(parser.parts)


def render(template, values, cloze_index, answer):
    if not isinstance(template, str) or len(template) > 32_000:
        return ""
    result = []
    active = True
    sections = []
    end = 0
    for match in TOKEN.finditer(template):
        if active:
            result.append(template[end:match.start()])
        end = match.end()
        token = match[1].strip()
        if token.startswith(("#", "^")):
            name = token[1:].strip()
            sections.append((name, active))
            active = active and (bool(values.get(name, "").strip()) == (token[0] == "#"))
        elif token.startswith("/"):
            if not sections or sections[-1][0] != token[1:].strip():
                return ""
            _, active = sections.pop()
        elif active and token:
            *filters, key = token.split(":")
            value = values.get(key.strip(), "")
            for op in reversed(filters):
                if op == "cloze":
                    def hide(match):
                        if int(match[1]) != cloze_index:
                            return match[2]
                        if answer:
                            return "<mark>" + match[2] + "</mark>"
                        return "<mark>[" + (match[3] or "…") + "]</mark>"
                    value = CLOZE.sub(hide, value)
                elif op == "text":
                    value = escape(plain_text(value))
                elif op == "type":
                    value = value if answer else ""
            result.append(value)
    if sections:
        return ""
    result.append(template[end:])
    return "".join(result)


def proto(data):
    """Read the scalar and length-delimited fields used by Anki package metadata."""
    if not isinstance(data, bytes):
        raise DeckImportError("Invalid package metadata.")
    offset = 0

    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                break
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise DeckImportError("Invalid package metadata.")

    while offset < len(data):
        key = varint()
        wire = key & 7
        if wire == 0:
            yield key >> 3, varint()
            continue
        size = varint() if wire == 2 else {1: 8, 5: 4}.get(wire)
        if size is None or offset + size > len(data):
            raise DeckImportError("Invalid package metadata.")
        yield key >> 3, data[offset:offset + size]
        offset += size


def member(archive, key, compressed, limit):
    with archive.open(key) as original:
        if compressed and zstd is None:
            raise DeckImportError("Modern Anki packages require Python 3.14 on the server.")
        options = {zstd.DecompressionParameter.window_log_max: 25} if compressed else None
        source = zstd.ZstdFile(original, options=options) if compressed else nullcontext(original)
        with source as stream:
            total = 0
            while chunk := stream.read(128 * 1024):
                total += len(chunk)
                if total > limit:
                    raise DeckImportError("The unpacked deck exceeds the import limits.")
                yield chunk


def members_are_bounded(upload):
    # Bound the central directory before ZipFile loads it into memory.
    upload.seek(0, io.SEEK_END)
    length = upload.tell()
    start = max(0, length - 65_557)
    upload.seek(start)
    tail = upload.read()
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or len(tail) - marker < 22:
        raise DeckImportError("This file is not an Anki deck package.")
    entries, directory_size = struct.unpack_from("<HI", tail, marker + 10)
    if start + marker >= 20:
        upload.seek(start + marker - 20)
        locator = upload.read(20)
        if locator.startswith(b"PK\x06\x07"):
            record_offset = struct.unpack_from("<Q", locator, 8)[0]
            if record_offset + 56 > start + marker - 20:
                raise DeckImportError("Invalid ZIP64 directory.")
            upload.seek(record_offset)
            record = upload.read(56)
            if len(record) != 56 or not record.startswith(b"PK\x06\x06"):
                raise DeckImportError("Invalid ZIP64 directory.")
            entries, directory_size = struct.unpack_from("<QQ", record, 32)
    if entries > MAX_PACKAGE_FILES:
        raise DeckImportError(f"This package has {entries:,} files. The limit is {MAX_PACKAGE_FILES:,} per package.")
    if directory_size > MAX_PACKAGE_DIRECTORY:
        raise DeckImportError("This package's file index exceeds 16 MiB. Export the deck in smaller packages.")
    upload.seek(0)


def anki_models(db, modern):
    if modern:
        models = {}
        for mid, config in db.execute("SELECT id, config FROM notetypes"):
            settings = dict(proto(config))
            models[mid] = (settings.get(1) == 1, settings.get(9) == 6, {}, {})
        for mid, order, name in db.execute("SELECT ntid, ord, fields.name FROM fields JOIN notetypes ON notetypes.id=fields.ntid"):
            models[mid][2][order] = name
        for mid, order, config in db.execute("SELECT ntid, ord, templates.config FROM templates JOIN notetypes ON notetypes.id=templates.ntid"):
            settings = dict(proto(config))
            models[mid][3][order] = (settings.get(1, b"").decode(), settings.get(2, b"").decode())
        return models

    models = json.loads(db.execute("SELECT models FROM col").fetchone()[0])
    return {
        int(mid): (model.get("type") == 1, model.get("originalStockKind") == 6,
                   {item["ord"]: item["name"] for item in model["flds"]},
                   {item["ord"]: (item["qfmt"], item["afmt"]) for item in model["tmpls"]})
        for mid, model in models.items()
    }


def read_package(upload, folder):
    members_are_bounded(upload)
    result = Deck()
    with ZipFile(upload) as archive:
        if len(archive.infolist()) > MAX_PACKAGE_FILES:
            raise DeckImportError(f"This package exceeds the {MAX_PACKAGE_FILES:,}-file limit.")
        names = set(archive.namelist())
        modern = "collection.anki21b" in names
        collection = next((name for name in ("collection.anki21b", "collection.anki21", "collection.anki2")
                           if name in names), None)
        if collection is None:
            raise DeckImportError("This package does not contain an Anki collection.")
        db_path = folder / "anki.sqlite3"
        with db_path.open("wb") as target:
            for chunk in member(archive, collection, modern, 128 * MIB):
                target.write(chunk)

        assets = {}
        if "media" in names:
            media_map = b"".join(member(archive, "media", modern, 16 * MIB))
            if modern:
                entries = (data for key, data in proto(media_map) if key == 1)
                assets = {dict(proto(entry))[1].decode(): str(i) for i, entry in enumerate(entries)}
            else:
                assets = {name: key for key, name in json.loads(media_map).items() if re.fullmatch(r"[0-9]+", key)}
        resolved = {}
        total_media = 0
        media_limit = min(10_000_000_000, max(0, shutil.disk_usage(folder).free - 2048 * MIB))
        deadline = time.monotonic() + 30 * 60

        def asset(source, tag):
            nonlocal total_media
            name = unquote(source)
            mime = MEDIA_TYPES.get(Path(name).suffix.lower(), "")
            if name not in assets or not mime.startswith(("image/" if tag == "img" else "audio/")):
                result.missing.add(name)
                return None
            if name not in resolved:
                key = assets[name]
                if key not in names:
                    result.missing.add(name)
                    return None
                hashed = hashlib.sha256(mime.encode() + b"\0")
                incoming = folder / "current-media"
                with incoming.open("wb") as target:
                    for chunk in member(archive, key, modern, min(2048 * MIB, media_limit - total_media)):
                        if time.monotonic() > deadline:
                            raise DeckImportError("This deck took too long to process.")
                        hashed.update(chunk)
                        target.write(chunk)
                digest = hashed.hexdigest()
                if digest not in result.media:
                    total_media += incoming.stat().st_size
                    path = folder / digest
                    incoming.replace(path)
                    result.media[digest] = (mime, path)
                else:
                    incoming.unlink()
                resolved[name] = digest
            return "/api/media/" + resolved[name]

        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            # Anki's indexes refer to this custom collation even when reading by integer IDs.
            def unicase(left, right):
                left, right = left.casefold(), right.casefold()
                return (left > right) - (left < right)

            db.create_collation("unicase", unicase)
            db.execute("PRAGMA trusted_schema=OFF")
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            models = anki_models(db, modern)
            total_text = 0
            rows = db.execute("""
                SELECT n.guid, n.mid, n.flds, n.tags, c.ord
                FROM cards c JOIN notes n ON n.id = c.nid ORDER BY c.id LIMIT ?
            """, (MAX_PACKAGE_CARDS + 1,))
            for number, (guid, mid, fields, tags, order) in enumerate(rows, 1):
                if number > MAX_PACKAGE_CARDS:
                    raise DeckImportError(f"Export decks with at most {MAX_PACKAGE_CARDS:,} cards per package.")
                if time.monotonic() > deadline:
                    raise DeckImportError("This deck took too long to process.")
                if mid not in models or len(fields) > 48_000:
                    result.skipped += 1
                    continue
                cloze, occlusion, field_names, templates = models[mid]
                template = templates.get(0 if cloze else order)
                if not template or occlusion:
                    result.skipped += 1
                    continue
                contents = fields.split("\x1f")
                values = {name: contents[index] for index, name in field_names.items() if 0 <= index < len(contents)}
                values["Tags"] = escape(tags)
                values["FrontSide"] = ""  # The review UI keeps the question visible.
                front, back = (clean_html(render(side, values, order + 1, answer), asset)
                               for answer, side in enumerate(template))
                if not front or not back or max(len(front), len(back)) > 12_000:
                    result.skipped += 1
                    continue
                total_text += len(front.encode()) + len(back.encode())
                if total_text > MAX_PACKAGE_CARD_TEXT:
                    raise DeckImportError("The deck contains too much card text. Split it into smaller decks.")
                result.cards.append((f"{guid}:{order}", front, back))
    return result


def read_tsv(upload):
    result = Deck()
    html = False
    header = True
    total_text = 0
    seen = set()
    with io.TextIOWrapper(upload, encoding="utf-8-sig", newline="") as source:
        for row in csv.reader(source, delimiter="\t", strict=True):
            if not row:
                continue
            if header and row[0].startswith("#") and (len(row) == 1 or row[0].startswith("#columns:")):
                html = html or row[0].strip().lower() == "#html:true"
                continue
            header = False
            if len(result.cards) + result.skipped >= MAX_CARDS:
                raise DeckImportError("Split the file into decks of at most 5,000 cards.")
            if len(row) < 2 or not all(value.strip() and len(value) <= 8_000 for value in row[:2]):
                result.skipped += 1
                continue
            render_cell = (lambda value: clean_html(value, lambda *_: None)) if html else (
                lambda value: escape(value.strip()).replace("\n", "<br>")
            )
            front, back = map(render_cell, row[:2])
            if not front or not back:
                result.skipped += 1
                continue
            key = hashlib.sha256(front.encode() + b"\0" + back.encode()).hexdigest()
            if key in seen:
                result.skipped += 1
                continue
            seen.add(key)
            total_text += len(front.encode()) + len(back.encode())
            if total_text > 12 * MIB:
                raise DeckImportError("The deck contains too much card text. Split it into smaller decks.")
            result.cards.append((key, front, back))
    return result


def read_deck(upload, suffix, folder):
    try:
        deck = read_package(upload, folder) if suffix == ".apkg" else read_tsv(upload)
    except DeckImportError:
        raise
    except (BadZipFile, sqlite3.Error, csv.Error, KeyError, IndexError, AttributeError, TypeError,
            RuntimeError, ValueError, EOFError, OSError) + ZSTD_ERRORS as error:
        raise DeckImportError("Could not read this deck. Try exporting it again from Anki.") from error
    if not deck.cards:
        raise DeckImportError("This file has no supported cards to import.")
    return deck
