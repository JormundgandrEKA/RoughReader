"""Every readable format behind one book interface (no Qt in here).

Three kinds of book:
  comic   CBZ: pages are images in a zip (archive.Book)
  fixed   PDF, XPS: pages are given by the file and drawn by MuPDF at the exact size shown
  reflow  EPUB, MOBI/AZW/AZW3/PRC, FB2/FBZ, TXT, Markdown, HTML, DOCX: text with no pages of its own;
          MuPDF lays it out into pages sized for the window and the chosen text style

Every book has len(), size(i) (page size in screen pixels at 100%), path, name, kind and close().
Fixed and reflow books also have render(i, width, height) -> PIL image.

MuPDF must never be used from two threads at once, so every call into it holds MUPDF.
"""

from __future__ import annotations

import base64
import html
import io
import os
import re
import struct
import threading
import zipfile
from dataclasses import dataclass, field, replace
from xml.etree import ElementTree

import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import archive, imaging, ocr

MUPDF = threading.RLock()
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

COMIC_EXT = {".cbz"}
FIXED_EXT = {".pdf", ".xps", ".oxps"}
KINDLE_EXT = {".mobi", ".azw", ".azw3", ".prc"}
REFLOW_EXT = {".epub", ".fb2", ".fbz", ".txt", ".md", ".markdown", ".html", ".htm", ".xhtml", ".docx"} | KINDLE_EXT
BOOK_EXT = COMIC_EXT | FIXED_EXT | REFLOW_EXT          # what the library lists
OPENABLE_EXT = BOOK_EXT | {".zip"}                     # what can be opened by hand
FORMAT_NAMES = {
    ".cbz": "CBZ", ".zip": "CBZ", ".pdf": "PDF", ".xps": "XPS", ".oxps": "XPS", ".epub": "EPUB",
    ".mobi": "MOBI", ".azw": "AZW", ".azw3": "AZW3", ".prc": "PRC", ".fb2": "FB2", ".fbz": "FB2",
    ".txt": "TXT", ".md": "MD", ".markdown": "MD", ".html": "HTML", ".htm": "HTML", ".xhtml": "HTML",
    ".docx": "DOCX",
}
PT_TO_PX = 96 / 72  # fixed pages: 100% means the printed size on a standard screen


class DocumentError(archive.BookError):
    """The document could not be opened (message is fit to show)."""


def extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def kind_of(path: str) -> str:
    ext = extension(path)
    if ext in COMIC_EXT or ext == ".zip":
        return "comic"
    if ext in FIXED_EXT:
        return "fixed"
    if ext in REFLOW_EXT:
        return "reflow"
    return ""


def format_name(path: str) -> str:
    return FORMAT_NAMES.get(extension(path), extension(path).lstrip(".").upper())


# ------------------------------------------------------------------ text style


@dataclass(frozen=True)
class TextStyle:
    """How reflowing text is set. Sizes are screen pixels at 100%."""

    size: int = 19
    family: str = "serif"        # serif | sans | original (the book's own fonts)
    spacing: str = "normal"      # compact | normal | relaxed | loose
    justify: bool = True
    paper: str = "#FFFFFF"       # page colour
    ink: str = "#000000"         # text colour (left alone when paper is white and ink black)

    def css(self) -> str:
        rules = [
            "@page { margin: 0 !important; }",
            "html, body { margin: 0 !important; padding: 0 !important; }",
        ]
        line = {"compact": 1.2, "normal": 1.4, "relaxed": 1.6, "loose": 1.85}.get(self.spacing)
        if line:
            rules.append(f"body, p, div, li, blockquote {{ line-height: {line} !important; }}")
        if self.family in ("serif", "sans"):
            generic = "serif" if self.family == "serif" else "sans-serif"
            rules.append(f"body, p, div, span, li, blockquote, td, a, em, i, b, strong {{ font-family: {generic} !important; }}")
        rules.append("p { text-align: %s; }" % ("justify" if self.justify else "left"))
        if not self.plain_colours:
            rules.append(f"* {{ color: {self.ink} !important; background: transparent !important; }}")
        return "\n".join(rules)

    @property
    def plain_colours(self) -> bool:
        return self.paper.upper() == "#FFFFFF" and self.ink.upper() in ("#000000", "#000")

    def margins(self) -> tuple[int, int]:
        """Space around the text on a page (left/right, top/bottom)."""
        return max(18, round(self.size * 1.7)), max(14, round(self.size * 1.3))

    def measure(self) -> int:
        """Widest comfortable line (about 70 characters), including the side margins."""
        return round(self.size * 34) + 2 * self.margins()[0]


# ------------------------------------------------------------------ sources


def _decode_text(data: bytes) -> str:
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def text_to_html(text: str, title: str = "") -> str:
    """Plain text to paragraphs: blank lines separate paragraphs; if there are none, every line is one."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    blocks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    parts = []
    for block in blocks:
        block = block.strip("\n")
        if not block.strip():
            continue
        lines = [html.escape(line.strip()) for line in block.split("\n")]
        parts.append("<p>" + "<br/>".join(lines) + "</p>")
    head = f"<title>{html.escape(title)}</title>" if title else ""
    return f"<html><head>{head}</head><body>{''.join(parts)}</body></html>"


def _xhtml(body_html: str, title: str) -> str:
    body = re.sub(r"(?is)^.*<body[^>]*>|</body>.*$", "", body_html)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            f"<title>{html.escape(title)}</title></head><body>{body}</body></html>")


def _headings(body_html: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Give every h1-h3 an id and list them: (level, id, text). Used for the book's contents."""
    found = []

    def mark(match):
        level, attrs, inner = int(match.group(1)), match.group(2), match.group(3)
        existing = re.search(r'id="([^"]+)"', attrs)
        anchor = existing.group(1) if existing else f"h{len(found) + 1}"
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        if text:
            found.append((level, anchor, text))
        return match.group(0) if existing else f'<h{level}{attrs} id="{anchor}">{inner}</h{level}>'

    body = re.sub(r"(?is)<h([1-3])([^>]*)>(.*?)</h\1>", mark, body_html)
    return body, found


def html_to_epub(body_html: str, images: dict[str, bytes] | None = None, title: str = "", author: str = "") -> bytes:
    """A minimal EPUB around one HTML file, so MuPDF lays it out like any book (and finds its images);
    its headings become the book's contents."""
    images = images or {}
    body_html, headings = _headings(body_html)
    points, stack, number = [], [], 0
    for level, anchor, text in headings:
        while stack and stack[-1] >= level:
            points.append("</navPoint>")
            stack.pop()
        number += 1
        points.append(f'<navPoint id="n{number}" playOrder="{number}"><navLabel><text>{html.escape(text)}</text></navLabel>'
                      f'<content src="text.xhtml#{anchor}"/>')
        stack.append(level)
    points += ["</navPoint>"] * len(stack)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        manifest = ['<item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>',
                    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
        for index, name in enumerate(images):
            media = "image/" + ("jpeg" if name.lower().endswith((".jpg", ".jpeg")) else extension(name).lstrip(".") or "png")
            manifest.append(f'<item id="img{index}" href="{html.escape(name)}" media-type="{media}"/>')
            zf.writestr(name, images[name])
        zf.writestr("book.opf",
                    '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    f"<dc:title>{html.escape(title or 'Untitled')}</dc:title><dc:creator>{html.escape(author)}</dc:creator>"
                    '<dc:identifier id="id">roughreader</dc:identifier><dc:language>en</dc:language></metadata>'
                    f"<manifest>{''.join(manifest)}</manifest><spine toc=\"ncx\"><itemref idref=\"text\"/></spine></package>")
        zf.writestr("toc.ncx",
                    '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    f'<head><meta name="dtb:uid" content="roughreader"/></head><docTitle><text>{html.escape(title or "Untitled")}</text></docTitle>'
                    f"<navMap>{''.join(points)}</navMap></ncx>")
        zf.writestr("text.xhtml", _xhtml(body_html, title))
    return out.getvalue()


def _docx_source(path: str) -> bytes:
    import mammoth

    images: dict[str, bytes] = {}

    def keep(image):
        with image.open() as stream:
            data = stream.read()
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif"}.get(image.content_type, ".png")
        name = f"image{len(images) + 1}{ext}"
        images[name] = data
        return {"src": name}

    with open(path, "rb") as handle:
        result = mammoth.convert_to_html(handle, convert_image=mammoth.images.img_element(keep))
    meta = read_metadata(path)
    return html_to_epub(result.value, images, meta.title, meta.author)


def _markdown_source(path: str) -> bytes:
    import markdown

    with open(path, "rb") as handle:
        text = _decode_text(handle.read()).replace("\r\n", "\n")
    text = re.sub(r"(?s)^---\n.*?\n---\n", "", text)  # front matter is metadata, not text
    body = markdown.markdown(text, extensions=["extra", "sane_lists"])
    images: dict[str, bytes] = {}
    folder = os.path.dirname(os.path.abspath(path))

    def pull(match):  # pictures next to the file come along
        src = html.unescape(match.group(2))
        local = os.path.normpath(os.path.join(folder, src))
        if "://" in src or not os.path.isfile(local):
            return match.group(0)
        name = f"image{len(images) + 1}{extension(local) or '.png'}"
        with open(local, "rb") as handle:
            images[name] = handle.read()
        return f'{match.group(1)}"{name}"'

    body = re.sub(r'(<img[^>]*?src=)"([^"]+)"', pull, body)
    meta = read_metadata(path)
    return html_to_epub(body, images, meta.title, meta.author)


def _open_source(path: str) -> "pymupdf.Document":
    """Open with MuPDF, converting the formats it cannot read itself. Caller holds MUPDF."""
    ext = extension(path)
    try:
        if ext == ".txt":
            with open(path, "rb") as handle:
                body = text_to_html(_decode_text(handle.read()))
            return pymupdf.open(stream=html_to_epub(body, title=archive.display_name(path)), filetype="epub")
        if ext in (".md", ".markdown"):
            return pymupdf.open(stream=_markdown_source(path), filetype="epub")
        if ext == ".docx":
            return pymupdf.open(stream=_docx_source(path), filetype="epub")
        if ext == ".fbz":
            with zipfile.ZipFile(path) as zf:
                inner = next((n for n in zf.namelist() if n.lower().endswith(".fb2")), None)
                if inner is None:
                    raise DocumentError("This FBZ archive has no FB2 book inside.")
                return pymupdf.open(stream=zf.read(inner), filetype="fb2")
        if ext in KINDLE_EXT:
            if _kindle_encrypted(path):
                raise DocumentError("This Kindle book is protected (DRM) and cannot be opened.")
            return pymupdf.open(path, filetype="mobi")
        return pymupdf.open(path)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"Could not open the document: {exc}") from exc


# ------------------------------------------------------------------ books


@dataclass
class TextLayer:
    """The words and links of one page, as fractions of the page as drawn (0..1, margins included).

    Word k of the page is word `first + k` of its `unit` (the chapter of a reflowing book, the page
    itself for fixed pages), which is what highlights and bookmarks remember: it survives re-layout."""

    unit: int
    first: int
    words: list            # (x0, y0, x1, y1, text, line id)
    links: list            # (x0, y0, x1, y1, target): target is (page, y fraction) or a URI string
    ocr: bool = False

    def text(self, start: int = 0, end: int | None = None) -> str:
        """The words start..end (inclusive), with line breaks where the lines break."""
        out, line = [], None
        for x0, y0, x1, y1, word, line_id in self.words[start:None if end is None else end + 1]:
            if line is not None and line_id != line:
                out.append("\n")
            elif out:
                out.append(" ")
            out.append(word)
            line = line_id
        return "".join(out).replace(" \n", "\n")


LAYER_CACHE = 160


def _keep(cache: dict, key, value):
    cache[key] = value
    while len(cache) > LAYER_CACHE:
        cache.pop(next(iter(cache)))
    return value


def _links(page, left: float, top: float, width: float, height: float, target_y) -> list:
    """Links on a page: internal ones as (page, y fraction on that page), external ones as URIs."""
    found = []
    for link in page.get_links():
        box = link.get("from")
        if box is None:
            continue
        rect = ((box.x0 + left) / width, (box.y0 + top) / height, (box.x1 + left) / width, (box.y1 + top) / height)
        if link.get("kind") == pymupdf.LINK_GOTO and link.get("page", -1) >= 0:
            to = link.get("to")
            found.append((*rect, (int(link["page"]), target_y(int(link["page"]), to.y if to is not None else 0.0))))
        elif link.get("kind") == pymupdf.LINK_URI and link.get("uri"):
            found.append((*rect, str(link["uri"])))
    return found


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pixmap_image(pix) -> Image.Image:
    mode = "RGBA" if pix.alpha else ("L" if pix.n == 1 else "RGB")
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _exact(image: Image.Image, width: int, height: int) -> Image.Image:
    """MuPDF can come out a pixel off; the view wants the size it asked for."""
    if image.size == (width, height):
        return image
    return image.resize((width, height), Image.Resampling.BILINEAR)


class FixedBook:
    """PDF and XPS: the file defines the pages."""

    kind = "fixed"

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        with MUPDF:
            self._doc = _open_source(self.path)
            if self._doc.needs_pass:
                self._doc.close()
                raise DocumentError("This document is password-protected.")
            if self._doc.page_count == 0:
                self._doc.close()
                raise DocumentError("This document has no pages.")
            self.sizes = [self._page_size(i) for i in range(self._doc.page_count)]
        self.name = read_metadata(self.path).title or archive.display_name(self.path)
        self.language = "en"
        self._layers: dict = {}

    def _page_size(self, index: int) -> tuple[int, int]:
        try:
            box = self._doc.page_cropbox(index)
            width, height = box.width, box.height
        except Exception:
            rect = self._doc[index].rect
            width, height = rect.width, rect.height
        return max(1, round(width * PT_TO_PX)), max(1, round(height * PT_TO_PX))

    def __len__(self) -> int:
        return len(self.sizes)

    def size(self, index: int) -> tuple[int, int]:
        return self.sizes[index]

    def scan_sizes(self, cache_dir: str = "") -> None:
        """Sizes are known from the start (rotated pages are corrected when first drawn)."""

    def render(self, index: int, width: int, height: int) -> Image.Image:
        with MUPDF:
            page = self._doc[index]
            rect = page.rect
            if (rect.width > rect.height) != (self.sizes[index][0] > self.sizes[index][1]):
                self.sizes[index] = (max(1, round(rect.width * PT_TO_PX)), max(1, round(rect.height * PT_TO_PX)))
            matrix = pymupdf.Matrix(width / rect.width, height / rect.height)
            image = _pixmap_image(page.get_pixmap(matrix=matrix, alpha=False))
        return _exact(image.convert("RGB") if image.mode != "RGB" else image, width, height)

    def toc(self) -> list[tuple[int, str, int]]:
        with MUPDF:
            return [(level, title, page - 1) for level, title, page, *_ in self._doc.get_toc(simple=True) if page > 0]

    def page_label(self, index: int) -> str:
        with MUPDF:
            try:
                return self._doc[index].get_label() or ""
            except Exception:
                return ""

    def text_layer(self, index: int, allow_ocr: bool = True) -> TextLayer:
        """Words and links of a page; a scanned page is read with OCR (once, then remembered)."""
        cached = self._layers.get(index)
        if cached is not None and (cached.words or cached.ocr or not allow_ocr):  # ocr: already tried
            return cached
        with MUPDF:
            page = self._doc[index]
            rect = page.rect
            width, height = rect.width, rect.height
            words = [(x0 / width, y0 / height, x1 / width, y1 / height, text, (block << 16) | line)
                     for x0, y0, x1, y1, text, block, line, _n in page.get_text("words")]
            links = _links(page, 0, 0, width, height, lambda p, y: y * PT_TO_PX / max(1, self.sizes[p][1]) if 0 <= p < len(self.sizes) else 0.0)
            pix = None
            if not words and allow_ocr and page.get_images() and ocr.available():
                zoom = min(3.0, 3000 / max(width, height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
        used = False
        if pix is not None:
            found = ocr.recognize(bytes(pix.samples), pix.width, pix.height, self.language)
            words = [(w.x0, w.y0, w.x1, w.y1, w.text, w.line) for w in found]
            used = True
        return _keep(self._layers, index, TextLayer(index, 0, words, links, used))

    def page_of_word(self, unit: int, index: int) -> int:
        return max(0, min(len(self.sizes) - 1, unit))

    def ocr_layer(self, index: int) -> TextLayer:
        """The page read by OCR even if it has a text layer (used where that layer is incomplete)."""
        with MUPDF:
            page = self._doc[index]
            zoom = min(3.0, 3000 / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
        found = ocr.recognize(bytes(pix.samples), pix.width, pix.height, self.language)
        return TextLayer(index, 0, [(w.x0, w.y0, w.x1, w.y1, w.text, w.line) for w in found], [], True)

    def close(self) -> None:
        with MUPDF:
            self._doc.close()


class ReflowBook:
    """Text without pages of its own. configure() lays it out for a page size and a text style;
    the layout can change at any time, and bookmarks carry a reading position across layouts."""

    kind = "reflow"

    def __init__(self, path: str, style: TextStyle | None = None):
        self.path = os.path.abspath(path)
        self.style = style or TextStyle()
        self._doc = None
        self._css = None
        self._page = (0, 0)          # whole page incl. margins, screen px at 100%
        self._layout = (0, 0, 0)     # text width, text height, font size
        self._count = 0
        self._layers: dict = {}
        self._wordcounts: dict[int, int] = {}
        self.scroll = False          # in scroll mode pages are drawn without top/bottom margins
        with MUPDF:
            self._reopen()
        meta = read_metadata(self.path)
        self.name = meta.title or archive.display_name(self.path)

    def _reopen(self) -> None:
        """(Re)open with the style's stylesheet; MuPDF reads user CSS only when a document opens."""
        css = self.style.css()
        old = self._doc
        pymupdf.mupdf.fz_set_user_css(css)
        try:
            doc = _open_source(self.path)
        finally:
            pymupdf.mupdf.fz_set_user_css("")
        if doc.needs_pass:
            doc.close()
            raise DocumentError("This document is password-protected.")
        if old is not None:
            old.close()
        self._doc, self._css, self._layout, self._count = doc, css, (0, 0, 0), 0
        self._layers, self._wordcounts = {}, {}

    def configure(self, page_width: int, page_height: int, style: TextStyle | None = None, keep=None) -> int:
        """Lay out for pages of this size (screen px, margins included). Returns the page now holding
        the position `keep` (from bookmark()), or 0."""
        with MUPDF:
            if style is not None and style != self.style:
                self.style = style
            if self.style.css() != self._css:
                self._reopen()
            mx, my = self.style.margins()
            text_w = max(120, page_width - 2 * mx)
            text_h = max(120, page_height - 2 * my)
            wanted = (text_w, text_h, self.style.size)
            if wanted != self._layout:
                self._doc.layout(width=text_w, height=text_h, fontsize=self.style.size)
                self._layout = wanted
                self._layers.clear()
                self._wordcounts.clear()
            self._page = (text_w + 2 * mx, text_h + 2 * my)
            self._count = self._doc.page_count
            if self._count == 0:
                raise DocumentError("This document has no text to show.")
            return self.page_of(keep) if keep is not None else 0

    def __len__(self) -> int:
        return self._count  # no lock: the UI asks often, and a layout may be running

    def size(self, index: int) -> tuple[int, int]:
        width, height = self._page
        if self.scroll:
            return width, height - 2 * self.style.margins()[1]
        return width, height

    def scan_sizes(self, cache_dir: str = "") -> None:
        """Pages come from configure()."""

    def bookmark(self, index: int) -> list:
        """A reading position that survives re-layout, reopening and restarts:
        [chapter, fraction through the chapter, the first words of the page]."""
        with MUPDF:
            index = max(0, min(index, self._doc.page_count - 1))
            chapter, page_in_chapter = self._doc.location_from_page_number(index)
            count = max(1, self._doc.chapter_page_count(chapter))
            words = _words(self._doc[index].get_text())[:60]
        return [int(chapter), page_in_chapter / count, words]

    def page_of(self, mark) -> int:
        """The page that now holds a position made by bookmark() (0 if it cannot be placed)."""
        if not isinstance(mark, (list, tuple)) or len(mark) < 2:
            return 0
        with MUPDF:
            try:
                chapter = max(0, min(int(mark[0]), self._doc.chapter_count - 1))
                count = self._doc.chapter_page_count(chapter)
                first = self._doc.page_number_from_location((chapter, 0))
                guess = max(0, min(count - 1, int(float(mark[1]) * count)))
                words = mark[2] if len(mark) > 2 else ""
                if words:
                    probe = words[:40]
                    for delta in (0, 1, -1, 2, -2, 3, -3, 4, -4):
                        page = guess + delta
                        if 0 <= page < count and probe in _words(self._doc[first + page].get_text()):
                            return first + page
                return first + guess
            except Exception:
                return 0

    def render(self, index: int, width: int, height: int) -> Image.Image:
        page_w, page_h = self.size(index)
        mx, my = self.style.margins()
        top = 0 if self.scroll else my
        scale_x, scale_y = width / page_w, height / page_h
        with MUPDF:
            page = self._doc[index]
            matrix = pymupdf.Matrix(scale_x, scale_y)
            text = _pixmap_image(page.get_pixmap(matrix=matrix, alpha=not self.style.plain_colours))
        canvas = Image.new("RGB", (width, height), self.style.paper)
        at = (round(mx * scale_x), round(top * scale_y))
        if text.mode == "RGBA":
            canvas.paste(text, at, text)
        else:
            canvas.paste(text.convert("RGB"), at)
        return canvas

    def toc(self) -> list[tuple[int, str, int]]:
        with MUPDF:
            return [(level, title, page - 1) for level, title, page, *_ in self._doc.get_toc(simple=True) if page > 0]

    def page_label(self, index: int) -> str:
        return ""

    def _word_count(self, page: int) -> int:
        """Caller holds MUPDF."""
        if page not in self._wordcounts:
            self._wordcounts[page] = len(self._doc[page].get_text("words"))
        return self._wordcounts[page]

    def text_layer(self, index: int, allow_ocr: bool = True) -> TextLayer:
        key = (index, self.scroll)
        cached = self._layers.get(key)
        if cached is not None:
            return cached
        page_w, page_h = self.size(index)
        mx, my = self.style.margins()
        top = 0 if self.scroll else my
        with MUPDF:
            page = self._doc[index]
            raw = page.get_text("words")
            self._wordcounts[index] = len(raw)
            words = [((x0 + mx) / page_w, (y0 + top) / page_h, (x1 + mx) / page_w, (y1 + top) / page_h, text, (block << 16) | line)
                     for x0, y0, x1, y1, text, block, line, _n in raw]
            links = _links(page, mx, top, page_w, page_h, lambda p, y: (y + top) / page_h)
            chapter, page_in_chapter = self._doc.location_from_page_number(index)
            start = index - page_in_chapter
            first = sum(self._word_count(p) for p in range(start, index))
        return _keep(self._layers, key, TextLayer(chapter, first, words, links))

    def page_of_word(self, unit: int, index: int) -> int:
        """The page that now holds word `index` of chapter `unit`."""
        with MUPDF:
            unit = max(0, min(unit, self._doc.chapter_count - 1))
            start = self._doc.page_number_from_location((unit, 0))
            count = self._doc.chapter_page_count(unit)
            seen = 0
            for page in range(start, start + count):
                seen += self._word_count(page)
                if index < seen:
                    return page
            return start + max(0, count - 1)

    def close(self) -> None:
        with MUPDF:
            if self._doc is not None:
                self._doc.close()


def open_book(path: str, style: TextStyle | None = None):
    kind = kind_of(path)
    if kind == "comic":
        book = archive.Book(path)
    elif kind == "fixed":
        book = FixedBook(path)
    elif kind == "reflow":
        book = ReflowBook(path, style)
    else:
        raise DocumentError(f"{format_name(path) or 'This'} files cannot be opened.")
    return book


# ------------------------------------------------------------------ metadata


@dataclass
class Meta:
    title: str = ""
    authors: list[tuple[str, str]] = field(default_factory=list)  # (name, role) with role '' for author
    publisher: str = ""
    date: str = ""
    language: str = ""
    series: str = ""
    number: str = ""
    format: str = ""

    @property
    def author(self) -> str:
        main = [name for name, role in self.authors if role in ("", "aut", "author", "writer")]
        return (main or [name for name, _ in self.authors] or [""])[0]


def _strip(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _epub_metadata(path: str) -> Meta:
    meta = Meta(format="EPUB")
    with zipfile.ZipFile(path) as zf:
        opf_path = _opf_path(zf)
        root = ElementTree.fromstring(zf.read(opf_path))
    ns = {"dc": "http://purl.org/dc/elements/1.1/", "opf": "http://www.idpf.org/2007/opf"}
    meta.title = _strip(root.findtext(".//dc:title", "", ns))
    roles = {}
    for item in root.iter("{http://www.idpf.org/2007/opf}meta"):  # EPUB 3 roles
        if item.get("property") == "role" and item.get("refines"):
            roles[item.get("refines").lstrip("#")] = _strip(item.text)
    for creator in root.findall(".//dc:creator", ns):
        role = creator.get("{http://www.idpf.org/2007/opf}role") or roles.get(creator.get("id") or "", "")
        name = _strip(creator.text)
        if name:
            meta.authors.append((name, role if role != "aut" else ""))
    meta.publisher = _strip(root.findtext(".//dc:publisher", "", ns))
    meta.date = _strip(root.findtext(".//dc:date", "", ns))
    meta.language = _strip(root.findtext(".//dc:language", "", ns))
    for item in root.iter("{http://www.idpf.org/2007/opf}meta"):
        if item.get("name") == "calibre:series":
            meta.series = _strip(item.get("content"))
        elif item.get("name") == "calibre:series_index":
            meta.number = _strip(item.get("content"))
    return meta


def _opf_path(zf: zipfile.ZipFile) -> str:
    container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    return rootfile.get("full-path")


def _palm_records(data: bytes) -> list[tuple[int, int]]:
    count = struct.unpack_from(">H", data, 76)[0]
    offsets = [struct.unpack_from(">I", data, 78 + 8 * i)[0] for i in range(count)]
    return [(offsets[i], offsets[i + 1] if i + 1 < count else len(data)) for i in range(count)]


def _kindle_header(path: str):
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 78 or data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
        raise DocumentError("Not a Kindle/MOBI book.")
    records = _palm_records(data)
    start, end = records[0]
    return data, records, data[start:end]


def _kindle_encrypted(path: str) -> bool:
    try:
        _data, _records, rec0 = _kindle_header(path)
    except Exception:
        return False
    return struct.unpack_from(">H", rec0, 12)[0] != 0


def _exth(rec0: bytes) -> dict[int, list[bytes]]:
    found: dict[int, list[bytes]] = {}
    if rec0[16:20] != b"MOBI":
        return found
    header_len = struct.unpack_from(">I", rec0, 20)[0]
    flags = struct.unpack_from(">I", rec0, 16 + 0x70)[0] if header_len >= 0x74 else 0
    start = 16 + header_len
    if not flags & 0x40 or rec0[start:start + 4] != b"EXTH":
        return found
    count = struct.unpack_from(">I", rec0, start + 8)[0]
    pos = start + 12
    for _ in range(count):
        kind, length = struct.unpack_from(">II", rec0, pos)
        found.setdefault(kind, []).append(rec0[pos + 8:pos + length])
        pos += length
    return found


def _kindle_metadata(path: str) -> Meta:
    meta = Meta(format=format_name(path))
    _data, _records, rec0 = _kindle_header(path)
    exth = _exth(rec0)
    encoding = "utf-8"
    if rec0[16:20] == b"MOBI" and struct.unpack_from(">I", rec0, 28)[0] == 1252:
        encoding = "cp1252"
    text = lambda value: _strip(value.decode(encoding, errors="replace"))  # noqa: E731
    meta.authors = [(text(v), "") for v in exth.get(100, [])]
    meta.publisher = text(exth[101][0]) if 101 in exth else ""
    meta.date = text(exth[106][0]) if 106 in exth else ""
    if 503 in exth:
        meta.title = text(exth[503][0])
    elif rec0[16:20] == b"MOBI":
        offset, length = struct.unpack_from(">II", rec0, 16 + 0x44)
        meta.title = text(rec0[offset:offset + length])
    return meta


def _fb2_root(path: str) -> ElementTree.Element:
    if extension(path) == ".fbz":
        with zipfile.ZipFile(path) as zf:
            inner = next(n for n in zf.namelist() if n.lower().endswith(".fb2"))
            return ElementTree.fromstring(zf.read(inner))
    return ElementTree.parse(path).getroot()


def _fb2_metadata(path: str) -> Meta:
    meta = Meta(format="FB2")
    root = _fb2_root(path)
    ns = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
    info = root.find(f"{ns}description/{ns}title-info")
    if info is None:
        return meta
    meta.title = _strip(info.findtext(f"{ns}book-title"))
    for author in info.findall(f"{ns}author"):
        parts = [_strip(author.findtext(f"{ns}{p}")) for p in ("first-name", "middle-name", "last-name")]
        name = " ".join(p for p in parts if p) or _strip(author.findtext(f"{ns}nickname"))
        if name:
            meta.authors.append((name, ""))
    sequence = info.find(f"{ns}sequence")
    if sequence is not None:
        meta.series, meta.number = _strip(sequence.get("name")), _strip(sequence.get("number"))
    meta.date = _strip(info.findtext(f"{ns}date"))
    publish = root.find(f"{ns}description/{ns}publish-info")
    if publish is not None:
        meta.publisher = _strip(publish.findtext(f"{ns}publisher"))
    return meta


def _docx_metadata(path: str) -> Meta:
    meta = Meta(format="DOCX")
    with zipfile.ZipFile(path) as zf:
        if "docProps/core.xml" not in zf.namelist():
            return meta
        root = ElementTree.fromstring(zf.read("docProps/core.xml"))
    ns = {"dc": "http://purl.org/dc/elements/1.1/", "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"}
    meta.title = _strip(root.findtext("dc:title", "", ns))
    creator = _strip(root.findtext("dc:creator", "", ns))
    if creator:
        meta.authors.append((creator, ""))
    return meta


def _text_metadata(path: str) -> Meta:
    meta = Meta(format=format_name(path))
    try:
        with open(path, "rb") as handle:
            head = _decode_text(handle.read(64 * 1024)).replace("\r\n", "\n")
    except OSError:
        return meta
    ext = extension(path)
    if ext in (".html", ".htm", ".xhtml"):
        found = re.search(r"(?is)<title[^>]*>(.*?)</title>", head)
        meta.title = _strip(html.unescape(re.sub(r"<[^>]+>", "", found.group(1)))) if found else ""
        author = re.search(r'(?is)<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)', head)
        if author:
            meta.authors.append((_strip(author.group(1)), ""))
    elif ext in (".md", ".markdown"):
        front = re.match(r"(?s)^---\n(.*?)\n---", head)
        if front:
            for key, value in re.findall(r"(?m)^(title|author):\s*(.+)$", front.group(1)):
                if key == "title":
                    meta.title = _strip(value.strip("\"'"))
                else:
                    meta.authors.append((_strip(value.strip("\"'")), ""))
        heading = re.search(r"(?m)^#\s+(.+)$", head)
        if heading and not meta.title:
            meta.title = _strip(heading.group(1))
    return meta


def _mupdf_metadata(path: str) -> Meta:
    meta = Meta(format=format_name(path))
    with MUPDF:
        doc = pymupdf.open(path)
        try:
            info = doc.metadata or {}
        finally:
            doc.close()
    meta.title = _strip(info.get("title"))
    author = _strip(info.get("author"))
    if author:
        meta.authors.append((author, ""))
    return meta


def _comic_metadata(path: str) -> Meta:
    meta = Meta(format="CBZ")
    try:
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith("comicinfo.xml")), None)
            root = ElementTree.fromstring(zf.read(name)) if name else None
    except (zipfile.BadZipFile, OSError, ElementTree.ParseError):
        root = None
    if root is not None:
        meta.title = _strip(root.findtext("Title"))
        meta.series, meta.number = _strip(root.findtext("Series")), _strip(root.findtext("Number"))
        for tag in ("Writer", "Penciller"):
            for name in _strip(root.findtext(tag)).split(","):
                if name.strip():
                    meta.authors.append((name.strip(), "" if tag == "Writer" else "art"))
        meta.publisher, meta.date = _strip(root.findtext("Publisher")), _strip(root.findtext("Year"))
    return meta


def read_metadata(path: str) -> Meta:
    """What the file says about itself. Never raises; unknown fields stay empty."""
    ext = extension(path)
    try:
        if ext == ".epub":
            return _epub_metadata(path)
        if ext in KINDLE_EXT:
            return _kindle_metadata(path)
        if ext in (".fb2", ".fbz"):
            return _fb2_metadata(path)
        if ext == ".docx":
            return _docx_metadata(path)
        if ext in (".txt", ".md", ".markdown", ".html", ".htm", ".xhtml"):
            return _text_metadata(path)
        if ext in FIXED_EXT:
            return _mupdf_metadata(path)
        if ext in COMIC_EXT or ext == ".zip":
            return _comic_metadata(path)
    except Exception:
        pass
    return Meta(format=format_name(path))


# ------------------------------------------------------------------ covers


def _epub_cover(path: str) -> bytes | None:
    with zipfile.ZipFile(path) as zf:
        opf_path = _opf_path(zf)
        root = ElementTree.fromstring(zf.read(opf_path))
        base = os.path.dirname(opf_path)
        ns = "{http://www.idpf.org/2007/opf}"
        items = {i.get("id"): i for i in root.iter(f"{ns}item")}
        chosen = next((i for i in items.values() if "cover-image" in (i.get("properties") or "")), None)
        if chosen is None:
            meta = next((m for m in root.iter(f"{ns}meta") if m.get("name") == "cover"), None)
            chosen = items.get(meta.get("content")) if meta is not None else None
        if chosen is None:
            chosen = next((i for i in items.values() if (i.get("media-type") or "").startswith("image/")
                           and "cover" in (i.get("id", "") + i.get("href", "")).lower()), None)
        if chosen is None or not (chosen.get("media-type") or "").startswith("image/"):
            return None
        href = os.path.normpath(os.path.join(base, chosen.get("href"))).replace("\\", "/")
        from urllib.parse import unquote

        return zf.read(unquote(href))


def _kindle_cover(path: str) -> bytes | None:
    data, records, rec0 = _kindle_header(path)
    exth = _exth(rec0)
    if 201 not in exth or rec0[16:20] != b"MOBI":
        return None
    first_image = struct.unpack_from(">I", rec0, 16 + 0x5C)[0]
    index = first_image + struct.unpack(">I", exth[201][0][:4])[0]
    if not 0 < index < len(records):
        return None
    start, end = records[index]
    return data[start:end]


def _fb2_cover(path: str) -> bytes | None:
    root = _fb2_root(path)
    ns = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
    image = root.find(f"{ns}description/{ns}title-info/{ns}coverpage/{ns}image")
    if image is None:
        return None
    ref = next((v for k, v in image.attrib.items() if k.endswith("href")), "").lstrip("#")
    binary = next((b for b in root.iter(f"{ns}binary") if b.get("id") == ref), None)
    return base64.b64decode(binary.text) if binary is not None and binary.text else None


def _first_page(path: str, size: tuple[int, int]) -> Image.Image | None:
    with MUPDF:
        doc = _open_source(path)
        try:
            if doc.is_reflowable:
                doc.layout(width=400, height=600, fontsize=11)
            page = doc[0]
            zoom = min(4.0, max(size[0] * 2 / page.rect.width, size[1] * 2 / page.rect.height))
            return _pixmap_image(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)).convert("RGB")
        finally:
            doc.close()


def _font(size: int, bold: bool = False):
    for name in (("bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf") if bold else ("bahnschrift.ttf", "segoeui.ttf", "arial.ttf")):
        for folder in (os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"), "/usr/share/fonts/truetype/dejavu"):
            try:
                return ImageFont.truetype(os.path.join(folder, name), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text_cover(title: str, author: str, fmt: str, size: tuple[int, int] = imaging.THUMB_SIZE) -> Image.Image:
    """A plain typographic cover for books without a picture: feldgrau, a burgundy band, the title."""
    width, height = size
    image = Image.new("RGB", size, "#3B4841")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, int(height * 0.62), width, int(height * 0.62) + 10], fill="#800020")
    draw.polygon([(0, 0), (int(width * 0.22), 0), (0, int(width * 0.22))], fill="#800020")
    title_font, author_font, tag_font = _font(int(width * 0.11), True), _font(int(width * 0.075)), _font(int(width * 0.06), True)
    words, lines, line = title.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=title_font) <= width * 0.84 or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    y = int(height * 0.14)
    for line in lines[:6]:
        draw.text((int(width * 0.08), y), line, fill="#D6D9D7", font=title_font)
        y += int(width * 0.135)
    if author:
        draw.text((int(width * 0.08), int(height * 0.68)), author, fill="#B3BCB7", font=author_font)
    draw.text((int(width * 0.08), int(height * 0.88)), fmt, fill="#D6D9D7", font=tag_font)
    return image


def cover_thumbnail(path: str, size: tuple[int, int] = imaging.THUMB_SIZE) -> Image.Image:
    """The book's cover cropped to a library tile."""
    ext = extension(path)
    data = None
    try:
        if ext in COMIC_EXT or ext == ".zip":
            data = archive.read_cover(path)
        elif ext == ".epub":
            data = _epub_cover(path)
        elif ext in KINDLE_EXT:
            data = _kindle_cover(path)
        elif ext in (".fb2", ".fbz"):
            data = _fb2_cover(path)
    except Exception:
        data = None
    if data:
        try:
            return imaging.thumbnail(data, size)
        except Exception:
            pass
    meta = read_metadata(path)
    if ext in FIXED_EXT or ext == ".epub":
        page = None
        try:
            page = _first_page(path, size)
        except Exception:
            page = None
        if page is not None:
            return ImageOps.fit(page, size, Image.Resampling.LANCZOS, centering=(0.5, 0.0))
    return text_cover(meta.title or archive.display_name(path), meta.author, format_name(path), size)


def with_style(style: TextStyle, **changes) -> TextStyle:
    return replace(style, **changes)
