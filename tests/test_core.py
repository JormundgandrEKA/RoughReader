import io, os, shutil, sys, threading, time, zipfile, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from roughreader import archive, layout, store, imaging, keymap, qtenv

# A real volume for the timing test; set ROUGHREADER_SAMPLE to one of your own.
SAMPLE = os.environ.get("ROUGHREADER_SAMPLE", "")  # a real CBZ volume, for the timing test (optional)


def make_zip(path, pages):
    with zipfile.ZipFile(path, "w") as zf:
        for name, size, mode in pages:
            buf = io.BytesIO()
            Image.new(mode, size, 128).save(buf, "PNG" if name.endswith("png") else "JPEG")
            zf.writestr(name, buf.getvalue())


def test_natural_sort_and_names():
    names = ["p10.jpg", "p2.jpg", "P1.jpg"]
    assert sorted(names, key=archive.natural_key) == ["P1.jpg", "p2.jpg", "p10.jpg"]
    assert archive.display_name("C:/x/Der Werwolf  v01 (2021) (Digital) (Ushi).cbz") == "Der Werwolf v01"
    assert archive.display_name("/x/(only tags).cbz") == "(only tags)"


def test_spreads():
    P, W = (1814, 2580), (3628, 2580)
    sizes = [P, P, P, W, P, P, P, W, P]
    assert layout.build_spreads(sizes, False) == [(i,) for i in range(9)]
    assert layout.build_spreads(sizes, True) == [(0,), (1, 2), (3,), (4, 5), (6,), (7,), (8,)]
    assert layout.build_spreads(sizes, True, cover_alone=False) == [(0, 1), (2,), (3,), (4, 5), (6,), (7,), (8,)]
    sp = layout.build_spreads(sizes, True)
    assert layout.spread_of(sp, 5) == 3 and layout.spread_of(sp, 0) == 0
    assert sorted(p for s in sp for p in s) == list(range(9))


def test_place():
    P = (1000, 1500)
    s, cw, ch, items = layout.place([P], 800, 600, "page", 1, False)
    assert (cw, ch) == (400, 600) and abs(s - 0.4) < 1e-9
    s, cw, ch, items = layout.place([P], 800, 600, "width", 1, False)
    assert (cw, ch) == (800, 1200)
    # two pages of different native height are matched in height; RTL puts page 0 on the right
    s, cw, ch, items = layout.place([(1000, 1500), (500, 750)], 2000, 1500, "page", 1, True)
    assert ch == 1500 and cw == 2000
    assert [i[0] for i in items] == [1, 0] and items[1][1] == 1000
    s, cw, ch, items = layout.place([P], 800, 600, "zoom", 2.0, False)
    assert (cw, ch) == (2000, 3000)


def test_book_and_edge_cases():
    with tempfile.TemporaryDirectory() as d:
        z = os.path.join(d, "Vol 2 [tag].cbz")
        make_zip(z, [("b/p10.jpg", (300, 200), "RGB"), ("b/p2.png", (20, 30), "L"),
                     ("__MACOSX/b/._p2.png", (5, 5), "L"), ("b/.hidden.jpg", (5, 5), "L")])
        with zipfile.ZipFile(z, "a") as zf:
            zf.writestr("b/readme.txt", "x"); zf.writestr("b/p3.jpg", b"not an image")
        b = archive.Book(z)
        assert len(b) == 3 and b.name == "Vol 2"
        b.scan_sizes()
        assert b.sizes == [(20, 30), None, (300, 200)]
        assert b.size(1) in ((20, 30), (300, 200))
        assert imaging.decode(b.read(0)).mode == "L"
        try: imaging.decode(b.read(1)); assert False
        except Exception: pass
        b.close()
        empty = os.path.join(d, "e.cbz")
        with zipfile.ZipFile(empty, "w") as zf: zf.writestr("a.txt", "x")
        for bad in (empty, os.path.join(d, "missing.cbz")):
            try: archive.Book(bad); assert False
            except archive.BookError: pass
        junk = os.path.join(d, "junk.cbz"); open(junk, "wb").write(b"hello")
        try: archive.Book(junk); assert False
        except archive.BookError: pass
        os.mkdir(os.path.join(d, "sub")); make_zip(os.path.join(d, "sub", "Vol 10.cbz"), [("a.jpg", (5, 5), "L")])
        make_zip(os.path.join(d, "Vol 1.cbz"), [("a.jpg", (5, 5), "L")])
        found = [os.path.relpath(p, d) for p in archive.list_books(d)]
        assert found[0] == "e.cbz" or "Vol 1.cbz" in found
        sib = [os.path.basename(p) for p in archive.sibling_books(z)]
        assert sib.index("Vol 1.cbz") < sib.index("Vol 2 [tag].cbz")


def test_store_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        s = store.Store(os.path.join(d, "nested"))
        assert s.get("fit") == "page" and s.progress("/a/b.cbz") == (0, 0)
        s.set("double", True); s.update_book("/a/b.cbz", page=41, count=182, rtl=True)
        s.save()
        t = store.Store(os.path.join(d, "nested"))
        assert t.get("double") is True and t.progress("/a/b.cbz") == (41, 182) and t.book("/a/b.cbz")["rtl"] is True
        assert t.adopt_progress("/a/b.cbz", "/lib/b.cbz") and t.progress("/lib/b.cbz") == (41, 182)
        t.update_book("/lib/c.cbz", page=3, count=9)
        assert not t.adopt_progress("/a/b.cbz", "/lib/c.cbz") and not t.adopt_progress("/none.cbz", "/lib/d.cbz")
        open(t.path, "w").write("{corrupt")
        assert store.Store(os.path.join(d, "nested")).get("fit") == "page"


def test_sample_volume():
    if not os.path.isfile(SAMPLE):
        print("\n  (skipped: no sample volume; set ROUGHREADER_SAMPLE)")
        return
    t = time.time(); b = archive.Book(SAMPLE); b.scan_sizes(); scan = time.time() - t
    assert len(b) > 0 and all(b.sizes)
    wide = [i for i, s in enumerate(b.sizes) if layout.is_wide(s)]
    if os.path.basename(SAMPLE).startswith("Der Werwolf  v01"):
        assert len(b) == 182 and len(wide) == 6
    sp = layout.build_spreads(b.sizes, True)
    assert all(len(s) == 1 for s in sp if s[0] in wide)
    t = time.time(); im = imaging.decode(b.read(min(20, len(b) - 1))); dec = time.time() - t
    t = time.time(); r = imaging.scale(im, 1012, 1440); sc = time.time() - t
    assert r.size == (1012, 1440)
    t = time.time(); th = imaging.thumbnail(archive.read_cover(SAMPLE)); tt = time.time() - t
    assert th.size == imaging.THUMB_SIZE
    print(f"\n  scan {len(b)} headers {scan*1000:.0f} ms | decode {dec*1000:.0f} ms | scale {sc*1000:.0f} ms | cover thumb {tt*1000:.0f} ms | wide pages {wide}")
    print("  spreads:", len(sp), "first:", sp[:6])
    b.close()


def test_keymap():
    K = keymap.Keymap
    all_keys = [k for c in keymap.COMMANDS for k in c.keys]
    assert len(all_keys) == len(set(all_keys)), "default keys clash"
    assert all(len(c.keys) <= keymap.MAX_KEYS for c in keymap.COMMANDS)
    assert keymap.split_parts("Ctrl+Shift+O") == ["Ctrl", "Shift", "O"]
    assert keymap.split_parts("Ctrl++") == ["Ctrl", "+"] and keymap.split_parts("+") == ["+"]
    m = K()
    assert m.overrides() == {} and m.owner("D") == "double" and m.keys("double") == ("D",)
    # plain rebind of a free key
    assert m.assign("double", 0, "Y") == (True, None) and m.keys("double") == ("Y",) and m.owner("D") is None
    # stealing a key from another command
    assert m.assign("rtl", 1, "W") == (True, "fit_width")
    assert m.keys("rtl") == ("R", "W") and m.keys("fit_width") == ()
    # moving a key between slots of one command
    assert m.assign("back", 0, "Backspace") == (True, None) and m.keys("back") == ("Backspace", "PgUp")
    # a full command replaces its last key when asked to add another
    assert m.assign("zoom_in", 3, "Z") == (True, None) and m.keys("zoom_in") == ("+", "=", "Z")
    # reserved and fixed keys
    assert m.assign("double", 1, "Esc") == (False, "leave_fullscreen")
    assert m.assign("leave_fullscreen", 0, "Q") == (False, None)
    m.remove("goto", 0); assert m.keys("goto") == ("Ctrl+G",)
    saved = m.overrides()
    assert set(saved) == {"double", "rtl", "fit_width", "back", "zoom_in", "goto"} and saved["fit_width"] == []
    # round trip
    n = K(saved)
    assert all(n.keys(c.id) == m.keys(c.id) for c in keymap.COMMANDS) and n.overrides() == saved
    now = [k for c in keymap.COMMANDS for k in n.keys(c.id)]
    assert len(now) == len(set(now))
    # an override claiming a key pushes the default owner off it, even without a matching entry
    o = K({"quit": ["D", "Ctrl+Q"]})
    assert o.keys("quit") == ("D", "Ctrl+Q") and o.keys("double") == ()
    # junk is ignored
    j = K({"nope": ["X"], "double": "D", "rtl": [5, "", "R", "R", "Esc"], "leave_fullscreen": ["Q"]})
    assert j.keys("double") == ("D",) and j.keys("rtl") == ("R",) and j.keys("leave_fullscreen") == ("Esc",)
    assert K("garbage").overrides() == {}
    m.reset(); assert m.overrides() == {}
    # survives the settings file
    with tempfile.TemporaryDirectory() as d:
        s = store.Store(d); s.set("keys", saved); s.save()
        assert K(store.Store(d).get("keys")).keys("rtl") == ("R", "W")


def test_qt_plugin_lookup():
    with tempfile.TemporaryDirectory() as d:
        pkg = os.path.join(d, "site-packages", "PySide6")
        os.makedirs(os.path.join(pkg, "plugins", "platforms"))
        # folder present but the Windows plugin itself missing -> unusable
        assert qtenv.locate_plugins(pkg, platform="win32") == ""
        open(os.path.join(pkg, "plugins", "platforms", "qwindows.dll"), "wb").close()
        assert qtenv.locate_plugins(pkg, platform="win32") == os.path.join(pkg, "plugins")
        # Linux/macOS wheels keep them under Qt/plugins
        other = os.path.join(d, "other", "PySide6")
        os.makedirs(os.path.join(other, "Qt", "plugins", "platforms"))
        open(os.path.join(other, "Qt", "plugins", "platforms", "libqxcb.so"), "wb").close()
        assert qtenv.locate_plugins(other, platform="linux") == os.path.join(other, "Qt", "plugins")
        assert qtenv.locate_plugins(other, platform="win32") == ""
        # a PyInstaller bundle is found through its bundle folder
        bundle = os.path.join(d, "dist", "_internal")
        os.makedirs(os.path.join(bundle, "PySide6", "plugins", "platforms"))
        open(os.path.join(bundle, "PySide6", "plugins", "platforms", "qwindows.dll"), "wb").close()
        assert qtenv.locate_plugins(os.path.join(d, "nowhere"), bundle, "win32") == os.path.join(bundle, "PySide6", "plugins")
    # settings left behind by other Qt software (Anaconda and friends) are dropped
    env = {"QT_PLUGIN_PATH": r"C:\Users\x\anaconda3\Library\plugins", "QT_QPA_PLATFORM_PLUGIN_PATH": "C:\\old",
           "QT_QPA_PLATFORM": "xcb", "PATH": "keep", "QT_SCALE_FACTOR": "1.25"}
    removed = qtenv.clean_environment(env, "win32")
    assert set(removed) == {"QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM"}
    assert env == {"PATH": "keep", "QT_SCALE_FACTOR": "1.25"}
    for keep in ("windows", "windows:darkmode=2", "offscreen"):
        env = {"QT_QPA_PLATFORM": keep}
        assert qtenv.clean_environment(env, "win32") == {} and env == {"QT_QPA_PLATFORM": keep}
    env = {"QT_QPA_PLATFORM": "offscreen"}
    assert qtenv.clean_environment(env, "linux") == {} and env
    assert qtenv.platform_file("win32") == "qwindows.dll"


def test_cached_cover_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.jpg")
        Image.new("RGB", imaging.THUMB_SIZE, (10, 200, 30)).save(path, "JPEG", quality=88)
        back = imaging.load_rgb(path)
        assert back.size == imaging.THUMB_SIZE and back.mode == "RGB"
        open(path, "wb").write(b"broken")
        try: imaging.load_rgb(path); assert False
        except Exception: pass


def test_build_arguments():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import build_exe
    assert os.path.isfile(build_exe.SPEC)
    args = build_exe.pyinstaller_args("OUT", "WORK")
    assert args[-1] == build_exe.SPEC and args[args.index("--distpath") + 1] == "OUT"
    assert build_exe.exe_path("OUT", True) == os.path.join("OUT", "RoughReader.exe")
    assert build_exe.exe_path("OUT", False) == os.path.join("OUT", "RoughReader", "RoughReader.exe")
    with tempfile.TemporaryDirectory() as d:
        # a single exe can only be checked for existing
        assert build_exe.missing_from_build(d, True) == ["the app itself (RoughReader.exe)"]
        open(os.path.join(d, "RoughReader.exe"), "wb").close()
        assert build_exe.missing_from_build(d, True) == []
        # a folder build is checked for Qt's display plugin, core library and the image encoders
        folder = os.path.join(d, "RoughReader"); internal = os.path.join(folder, "_internal")
        os.makedirs(os.path.join(internal, "PySide6", "plugins", "platforms")); os.makedirs(os.path.join(internal, "PIL"))
        assert len(build_exe.missing_from_build(d, False)) == 4
        webp = "_webp.cp%d%d-win_amd64.pyd" % sys.version_info[:2]
        for path in (os.path.join(folder, "RoughReader.exe"), os.path.join(internal, "PySide6", "Qt6Core.dll"),
                     os.path.join(internal, "PySide6", "plugins", "platforms", "qwindows.dll"), os.path.join(internal, "PIL", webp)):
            open(path, "wb").close()
        assert build_exe.missing_from_build(d, False) == []


def test_selftest_sample_and_report():
    from roughreader import selftest
    with tempfile.TemporaryDirectory() as d:
        b = archive.Book(selftest.make_sample(d)); b.scan_sizes()
        assert layout.build_spreads(b.sizes, True) == [(0,), (1, 2), (3,), (4, 5)]
        b.close()


def test_shrink():
    from roughreader import selftest, shrink
    with tempfile.TemporaryDirectory() as d:
        src = selftest.make_sample(d)
        with zipfile.ZipFile(src, "a") as zf:
            zf.writestr("ComicInfo.xml", "<ComicInfo/>")
        src_pages = archive.Book(src)
        seen = []
        out = shrink.default_output(src, "720p")
        assert out.endswith(" - 720p.cbz") and os.path.dirname(out) == os.path.dirname(src)
        for fmt in ("webp", "avif"):
            out = shrink.default_output(src, "720p")
            res = shrink.shrink(src, out, 500, fmt, 70, progress=lambda a, b: seen.append((a, b)))
            assert res.pages == 6 and res.kept_original == 0 and not os.path.exists(out + ".part")
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
                assert names[:6] == [f"000{i}.{fmt}" for i in range(1, 7)] and "ComicInfo.xml" in names
            b = archive.Book(out); b.scan_sizes()
            assert len(b) == 6 and [s[1] for s in b.sizes] == [500] * 6      # reduced, never enlarged
            assert b.sizes[3][0] == round(1200 * 500 / 850) and b.sizes[0][0] == round(600 * 500 / 850)
            b.close()
            assert shrink.default_output(src, "720p") != out                  # never reuses a name
            assert shrink.estimate(src, 500, fmt, 70) > 0
        assert seen[-1] == (6, 6)
        small = shrink.default_output(src, "4k")
        shrink.shrink(src, small, 2400, "webp", 80)                            # target above source: not enlarged
        b = archive.Book(small); b.scan_sizes(); assert b.sizes[0][1] == 850; b.close()
        # failure modes leave nothing behind and never touch the original
        stop = threading.Event(); stop.set()
        target = os.path.join(d, "cancelled.cbz")
        try:
            shrink.shrink(src, target, 500, cancel=stop); assert False
        except shrink.Cancelled:
            pass
        assert not os.path.exists(target) and not os.path.exists(target + ".part")
        for bad in (lambda: shrink.shrink(src, src, 500), lambda: shrink.shrink(src, small, 500),
                    lambda: shrink.shrink(os.path.join(d, "missing.cbz"), target, 500)):
            try:
                bad(); assert False
            except shrink.ShrinkError:
                pass
        # a page that cannot be decoded is kept as it was
        broken = os.path.join(d, "broken.cbz")
        with zipfile.ZipFile(broken, "w") as zf:
            zf.writestr("a.jpg", b"not an image"); zf.writestr("b.jpg", zipfile.ZipFile(src).read("page_001.jpg"))
        res = shrink.shrink(broken, os.path.join(d, "fixed.cbz"), 500)
        assert res.pages == 2 and res.kept_original == 1
        src_pages.close()
        assert shrink.preset("nonsense").id == shrink.DEFAULT_PRESET


def test_portable_storage():
    saved = (getattr(sys, "frozen", None), sys.executable, os.environ.get("APPDATA"))
    with tempfile.TemporaryDirectory() as d:
        appdata = os.path.join(d, "appdata"); os.makedirs(os.path.join(appdata, "RoughReader"))
        open(os.path.join(appdata, "RoughReader", "state.json"), "w").write('{"settings": {"fit": "width", "library": "D:/x", "sources": ["D:/y"]}, "books": {}}')
        exe_dir = os.path.join(d, "stick"); os.makedirs(exe_dir)
        try:
            os.environ["APPDATA"] = appdata
            assert qtenv.portable_dir() == "" and qtenv.data_dir() == os.path.join(appdata, "RoughReader")  # from source
            sys.frozen = True; sys.executable = os.path.join(exe_dir, "RoughReader.exe")
            data = os.path.join(exe_dir, qtenv.PORTABLE_FOLDER)
            assert qtenv.portable_dir() == data and qtenv.data_dir() == data
            assert qtenv.cache_dir() == os.path.join(data, "cache")
            assert qtenv.adopt_existing_settings() is True                      # first run takes over the old settings
            adopted = store.Store(data)
            assert adopted.get("fit") == "width" and adopted.get("library") == "" and adopted.get("sources") == []
            open(os.path.join(data, "state.json"), "w").write('{"settings": {"fit": "height"}}')
            assert qtenv.adopt_existing_settings() is False                     # never overwrites the portable copy
            assert store.Store(data).get("fit") == "height"
            assert os.path.isfile(os.path.join(appdata, "RoughReader", "state.json"))  # the original stays
        finally:
            if saved[0] is None:
                del sys.frozen
            else:
                sys.frozen = saved[0]
            sys.executable = saved[1]
            if saved[2] is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = saved[2]


def test_colour_schemes():
    from roughreader import theme
    def lum(c):
        f = lambda v: v / 255 / 12.92 if v / 255 <= 0.03928 else ((v / 255 + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())
    def contrast(a, b):
        x, y = sorted((lum(a), lum(b)), reverse=True)
        return (x + 0.05) / (y + 0.05)
    names = theme.scheme_names()
    assert len(names) == 6 and len(set(names)) == 6 and theme.DEFAULT_SCHEME == "Feldgrau" and "Feldgrau" in names
    lums = []
    try:
        for name in names:
            assert theme.set_scheme(name) == name and theme.SCHEME == name
            assert contrast(theme.GREY, theme.FELD) >= 4.5, name               # text on the bars
            assert contrast(theme.GREY, theme.FELD_DARK) >= 4.5, name          # text in inputs
            assert contrast(theme.GREY_DIM, theme.FELD) >= 3.5, name           # secondary text
            assert contrast(theme.GREY, theme.FELD_LIGHT) >= 3.0, name         # icons on the hover wash
            assert contrast(theme.ON_ACCENT, theme.BURG) >= 4.5 and contrast(theme.ON_ACCENT, theme.BURG_HI) >= 4.5, name
            assert contrast(theme.FELD_DEEP, theme.QColor("white")) >= 1.5, name  # canvas distinct from a white page
            lums.append(lum(theme.FELD))
        assert lums == sorted(lums) or lums[0] < lums[-1]                      # dark ... bright
        assert lums[0] < 0.02 and lums[-1] > 0.85                              # extremely dark to bright white
        assert theme.set_scheme("nonsense") == "Feldgrau"
        assert theme.u(30) == 40 and abs(theme.font(10).pointSizeF() - 10 * theme.TEXT_SCALE) < 1e-9
    finally:
        theme.set_scheme("Feldgrau")
    assert store.Store(tempfile.mkdtemp()).get("scheme") == "Feldgrau" and store.Store(tempfile.mkdtemp()).get("borders") is False



def make_mobi(path, html, title="Mobi Book", author="Mo Writer"):
    """A minimal unencrypted MOBI (PalmDB, uncompressed text records, EXTH title and author)."""
    import struct
    text = html.encode("utf-8")
    recs = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    full = title.encode("utf-8")
    exth_recs = [(100, author.encode("utf-8")), (503, full)]
    body = b"".join(struct.pack(">II", t, len(d) + 8) + d for t, d in exth_recs)
    exth = b"EXTH" + struct.pack(">II", 12 + len(body), len(exth_recs)) + body
    exth += b"\0" * ((4 - len(exth) % 4) % 4)
    mobi = bytearray(0xE8)
    mobi[0:4] = b"MOBI"
    for off, value in ((4, 0xE8), (8, 2), (12, 65001), (16, 1), (20, 6), (64, len(recs) + 1),
                       (68, 16 + 0xE8 + len(exth)), (72, len(full)), (88, 6), (92, 0xFFFFFFFF), (112, 0x40)):
        struct.pack_into(">I", mobi, off, value)
    rec0 = struct.pack(">HHIHHHH", 1, 0, len(text), len(recs), 4096, 0, 0) + bytes(mobi) + exth + full + b"\0\0"
    records = [rec0] + recs
    header = bytearray(78)
    header[0:8] = b"testbook"
    header[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", header, 76, len(records))
    offset, table = 78 + 8 * len(records) + 2, b""
    for i, r in enumerate(records):
        table += struct.pack(">IB", offset, 0) + (2 * i).to_bytes(3, "big")
        offset += len(r)
    with open(path, "wb") as f:
        f.write(bytes(header) + table + b"\0\0" + b"".join(records))


def make_docx(path, paragraphs, title="Docx Title", author="Doc Writer"):
    """A minimal Word document (enough for mammoth)."""
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        zf.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    f"<w:body>{body}</w:body></w:document>")
        zf.writestr("docProps/core.xml", '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                    f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></cp:coreProperties>')


def test_documents_every_format():
    import pymupdf
    from roughreader import documents as D
    paras = "".join(f"<p>Paragraph {i}. " + "The quick brown fox jumps over the lazy dog. " * 8 + "</p>" for i in range(40))
    html_doc = f'<html><head><title>Html Title</title><meta name="author" content="Web Writer"/></head><body><h1>Html Title</h1>{paras}</body></html>'
    with tempfile.TemporaryDirectory() as d:
        files = {}
        files["epub"] = os.path.join(d, "a.epub"); open(files["epub"], "wb").write(D.html_to_epub(paras, title="Epub Title", author="Eve Writer"))
        files["mobi"] = os.path.join(d, "b.mobi"); make_mobi(files["mobi"], "<html><body>" + paras + "</body></html>")
        files["azw3"] = os.path.join(d, "c.azw3"); make_mobi(files["azw3"], "<html><body>" + paras + "</body></html>", title="Kindle Title")
        files["html"] = os.path.join(d, "d.html"); open(files["html"], "w", encoding="utf-8").write(html_doc)
        files["txt"] = os.path.join(d, "e.txt"); open(files["txt"], "w", encoding="cp1252").write("Caf\xe9 notes\n\n" + "\n\n".join("Plain words here. " * 20 for _ in range(30)))
        files["md"] = os.path.join(d, "f.md"); open(files["md"], "w", encoding="utf-8").write("---\ntitle: Md Title\nauthor: Mark Down\n---\n# Heading\n\n" + "Some *markdown* text. " * 200)
        files["fb2"] = os.path.join(d, "g.fb2"); open(files["fb2"], "w", encoding="utf-8").write(
            '<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>'
            '<author><first-name>Fiona</first-name><last-name>Book</last-name></author><book-title>Fb Title</book-title></title-info></description>'
            '<body><section><p>' + "Fiction book text. " * 300 + '</p></section></body></FictionBook>')
        files["fbz"] = os.path.join(d, "h.fbz")
        with zipfile.ZipFile(files["fbz"], "w") as zf:
            zf.write(files["fb2"], "inner.fb2")
        files["docx"] = os.path.join(d, "i.docx"); make_docx(files["docx"], ["Word text number %d. " % i * 10 for i in range(40)])
        files["pdf"] = os.path.join(d, "j.pdf")
        with D.MUPDF:
            doc = pymupdf.open()
            for n in range(4):
                doc.new_page(width=300, height=500 if n != 2 else 300).insert_text((30, 60), f"page {n}")
            doc.set_metadata({"title": "Pdf Title", "author": "Jünger, Ernst, 1895-1998"})
            doc.save(files["pdf"])
            doc.close()
        expect = {"epub": ("Epub Title", "Eve Writer"), "mobi": ("Mobi Book", "Mo Writer"), "azw3": ("Kindle Title", "Mo Writer"),
                  "html": ("Html Title", "Web Writer"), "md": ("Md Title", "Mark Down"), "fb2": ("Fb Title", "Fiona Book"),
                  "fbz": ("Fb Title", "Fiona Book"), "docx": ("Docx Title", "Doc Writer"), "pdf": ("Pdf Title", "Jünger, Ernst, 1895-1998")}
        for key, path in files.items():
            meta = D.read_metadata(path)
            if key in expect:
                assert (meta.title, meta.author) == expect[key], (key, meta.title, meta.author)
            book = D.open_book(path)
            assert book.kind == ("fixed" if key == "pdf" else "reflow"), key
            if book.kind == "reflow":
                book.configure(500, 700)
            assert len(book) >= 1, key
            w, h = book.size(0)
            image = book.render(0, w, h)
            assert image.size == (w, h) and image.mode == "RGB", key
            assert D.cover_thumbnail(path).size == imaging.THUMB_SIZE, key
            book.close()
        # fixed pages keep their own sizes (a landscape page among portrait ones)
        pdf = D.open_book(files["pdf"])
        assert pdf.size(0) == (400, 667) and pdf.size(2) == (400, 400) and pdf.render(1, 200, 333).size == (200, 333)
        pdf.close()
        # reflow: a reading position survives re-layout, a style change (reopen) and a fresh instance
        book = D.open_book(files["epub"])
        book.configure(500, 700)
        count = len(book)
        mark = book.bookmark(count // 2)
        start = D._words(book._doc[count // 2].get_text())[:30]
        bigger = D.TextStyle(size=26, family="sans", spacing="loose", paper="#F4ECD8", ink="#3B2F23")
        page = book.configure(500, 700, bigger, keep=mark)
        assert len(book) > count and start in D._words(book._doc[page].get_text())
        assert book.render(page, *book.size(page)).getpixel((2, 2)) == (0xF4, 0xEC, 0xD8)   # page tone
        again = D.open_book(files["epub"], bigger)
        again.configure(500, 700, bigger)
        assert again.page_of(mark) == page and again.page_of(None) == 0 and again.page_of(12345) == 0
        book.scroll = True                       # scroll mode drops the top and bottom margins
        assert book.size(0)[1] == 700 - 2 * bigger.margins()[1]
        book.close()
        again.close()
        # unknown and damaged files fail with a readable error
        for name, data in (("x.epub", b"not a zip"), ("y.pdf", b"%PDF-broken"), ("z.xyz", b"")):
            bad = os.path.join(d, name)
            open(bad, "wb").write(data)
            try:
                b = D.open_book(bad)
                if b.kind == "reflow":
                    b.configure(400, 600)
                assert len(b) == 0, name
            except archive.BookError:
                pass
        assert D.kind_of("a.CBZ") == "comic" and D.kind_of("a.AZW3") == "reflow" and D.kind_of("a.oxps") == "fixed" and D.kind_of("a.doc") == ""


def test_organize_names():
    from roughreader import organize as O
    assert O.clean_author("Jünger, Ernst, 1895-1998, author") == "Ernst Jünger"
    assert O.clean_author("Orwell, George, 1903-1950") == "George Orwell"
    assert O.clean_author("Abendroth, Joss Santiago [Abendroth, Joss Santiago]") == "Joss Santiago Abendroth"
    assert O.split_authors("Jünger, Ernst;Hansen, Thomas S(Translator);Hansen, Abby") == ["Ernst Jünger", "Thomas S Hansen", "Abby Hansen"]
    assert O.split_authors("Antoine de Saint-Exupéry, Alan Wakeman") == ["Antoine de Saint-Exupéry", "Alan Wakeman"]
    assert O.split_authors("Isuna, Hasekura") == ["Hasekura Isuna"]
    assert O.surname("Antoine de Saint-Exupéry") == "Saint-Exupéry" and O.surname("Ludwig van Beethoven") == "Beethoven"
    assert O.clean_title("STARSHIP TROOPERS") == "Starship Troopers"
    assert O.clean_title("READINGS IN MEDIEVAL HISTORY: Fifth Edition") == "Readings in Medieval History: Fifth Edition"
    assert O.clean_title("The Storm of Steel_ Original 1929 Translation") == "The Storm of Steel: Original 1929 Translation"
    assert O.work_key("The Storm of Steel: Original 1929 Translation") == O.work_key("Storm of steel") == "storm of steel"
    assert O.work_key("Der Zauberberg") == "zauberberg" and O.work_key("A") == "a"
    nfd = "Jünger"
    assert O.fold(nfd) == O.fold("Jünger") == "junger" and O.nfc(nfd) == "Jünger"
    assert O.safe_name('a: b / c? "d"') == "a - b c d" and len(O.safe_name("x" * 300)) <= 90
    p = O.parse_filename("1984 -- George Orwell -- London, England, 2013 -- Arcturus -- 9781782124207 -- 6a9b077cacd1377b8c330f5b327d559a -- Anna's Archive.pdf")
    assert (p.title, p.authors, p.publisher, p.year, p.anna) == ("1984", ["George Orwell"], "Arcturus", "2013", True)
    p = O.parse_filename("Eumeswil -- Jünger, Ernst -- 1993 -- Harmondsworth _ Penguin -- 9780140029857 -- e60951c03e30a0d192f8429a5d0eb08b -- Anna’s Archive.epub")
    assert (p.authors, p.publisher, p.year) == (["Ernst Jünger"], "Penguin", "1993"), (p.authors, p.publisher, p.year)
    p = O.parse_filename("x -- Someone -- European Perspectives_ A Series in -- 9780231127400 -- da74ec93a88486c315ab2c69dbb0777c -- Anna’s Archive.pdf")
    assert p.publisher == ""
    p = O.parse_filename("Readings in Medieval History, Fifth Edition by Patrick J. Geary (editor).pdf")
    assert (p.title, p.authors) == ("Readings in Medieval History, Fifth Edition", ["Patrick J. Geary"])
    p = O.parse_filename("Spengler, Oswald - Decline of the West (Random Shack, 2016).epub")
    assert (p.title, p.authors, p.edition, p.year) == ("Decline of the West", ["Oswald Spengler"], ["Random Shack"], "2016")
    assert O.parse_filename("Starship Troopers - Heinlein, Robert A.epub").authors == ["Robert A Heinlein"]
    assert O.parse_filename("Unit 731 Testimony - Hal Gold.pdf").authors == ["Hal Gold"]
    assert O.parse_filename("Jünger - Copse 125.pdf", known=lambda s: s == "Jünger").title == "Copse 125"
    assert O.parse_filename("Some Long Title Without Author.txt").authors == []


def test_organize_import_and_scan():
    from roughreader import documents as D, organize as O, selftest
    with tempfile.TemporaryDirectory() as d:
        src, root = os.path.join(d, "src"), os.path.join(d, "Library")
        os.makedirs(os.path.join(src, "deeper"))
        text = "".join("<p>" + "Words. " * 50 + "</p>" for _ in range(5))
        open(os.path.join(src, "Storm of Steel - Ernst Jünger (Penguin Classics Deluxe Edition).epub"), "wb").write(
            D.html_to_epub(text, title="Storm of steel", author="Jünger, Ernst, 1895-1998, author"))
        open(os.path.join(src, "deeper", "The Storm of Steel_ Original 1929 Translation -- Ernst Jünger -- Paperback, 2019 -- Mystery Grove -- 38b4931c7e1ccba8122e74b2f219e4d9 -- Anna’s Archive.epub"), "wb").write(
            D.html_to_epub(text + "<p>other edition</p>", title="The Storm of Steel: Original 1929 Translation", author="Ernst Jünger"))
        open(os.path.join(src, "Jünger - Copse 125.txt"), "w").write("Copse text\n\nMore text.")
        comic = selftest.make_sample(src)
        shutil.copy(comic, os.path.join(src, "deeper", "duplicate.cbz"))      # identical bytes: copied once
        open(os.path.join(src, "readme.nfo"), "w").write("not a book")
        index = O.Index(root)
        plan = O.plan_import([src], root, index)
        assert len(plan.copies) == 4 and len(plan.duplicates) == 1, (len(plan.copies), plan.duplicates)
        done, errors = O.run_import(plan, index)
        assert done == 4 and not errors and len(plan.copied) == 4 and all(os.path.isfile(c) for _s, c in plan.copied) and os.path.isfile(os.path.join(root, O.INDEX_NAME))
        authors = O.scan(root)
        names = [a.name for a in authors]
        assert names == ["Ernst Jünger", "Selftest"], names
        works = {w.title: w for w in authors[0].works}
        assert set(works) == {"Storm of Steel", "Copse 125"}, set(works)
        storm = works["Storm of Steel"]
        labels = sorted(v.label for v in storm.variants)
        assert labels == ["Original 1929 Translation, Mystery Grove, 2019", "Penguin Classics Deluxe Edition"], labels
        assert all(os.path.dirname(v.path) == storm.folder for v in storm.variants)
        assert authors[1].works[0].title == "Selftest v01" and authors[1].works[0].variants[0].format == "CBZ"
        # nothing new: a second import of the same folder copies nothing; a new file is picked up
        again = O.plan_import([src], root, O.Index(root), only_new=True)
        assert not again.copies and not again.duplicates
        open(os.path.join(src, "Eumeswil -- Jünger, Ernst -- 1993 -- Penguin -- e60951c03e30a0d192f8429a5d0eb08b -- Anna’s Archive.epub"), "wb").write(
            D.html_to_epub(text + "<p>e</p>", title="Eumeswil", author="Ernst Jünger"))
        index = O.Index(root)
        newer = O.plan_import([src], root, index, only_new=True)
        assert len(newer.copies) == 1 and os.path.basename(os.path.dirname(newer.copies[0][1])) == "Eumeswil"
        O.run_import(newer, index)
        # a shrunk copy lands beside its original and shows up as a variant of the same work
        volume = authors[1].works[0].variants[0].path
        assert O.destination_folder(volume, root) == os.path.dirname(volume)
        assert O.destination_folder(comic, root) == os.path.dirname(volume)            # from outside the library too
        shutil.copy(volume, os.path.join(os.path.dirname(volume), "Selftest v01 - 1080p.cbz"))
        shutil.copy(volume, os.path.join(root, "Selftest", "Selftest v02.cbz"))       # a loose file is a work of its own
        series = next(a for a in O.scan(root) if a.name == "Selftest")
        assert [v.label for v in series.works[0].variants] == ["", "1080p"], [v.label for v in series.works[0].variants]
        assert O.next_work(volume, root).endswith("Selftest v02.cbz") and O.next_work(comic, root) == ""


def make_book_pdf(path, title="Old Book", author="Ann Example", copyright_text=None, contents=True, pages=40):
    """A PDF like a printed book: title page, copyright page, a contents page, chapters with running numbers."""
    import pymupdf
    from roughreader import documents as D
    copyright_text = copyright_text or ("First published in 1931\nThis edition published 2004 by Example Press\n"
                                        "Copyright © 2004 by Ann Example\nAll rights reserved\nISBN 978-0-14-310825-2")
    with D.MUPDF:
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=600)
        page.insert_text((60, 200), title, fontsize=24)
        page.insert_text((60, 240), author, fontsize=14)
        page = doc.new_page(width=400, height=600)
        page.insert_textbox(pymupdf.Rect(40, 300, 360, 560), copyright_text, fontsize=9)
        if contents:
            page = doc.new_page(width=400, height=600)
            page.insert_text((60, 60), "Contents", fontsize=16)
            for n, (name, printed) in enumerate((("The Beginning", 1), ("The Middle Part", 11), ("The End", 21))):
                page.insert_text((60, 110 + 30 * n), name, fontsize=11)
                page.insert_text((320, 110 + 30 * n), str(printed), fontsize=11)
        first = doc.page_count
        for number in range(1, pages + 1):
            page = doc.new_page(width=400, height=600)
            if number in (1, 11, 21):
                page.insert_text((60, 80), {1: "The Beginning", 11: "The Middle Part", 21: "The End"}[number], fontsize=16)
            page.insert_textbox(pymupdf.Rect(40, 100, 360, 540), ("Words of page %d. " % number) * 30, fontsize=10)
            page.insert_text((195, 580), str(number), fontsize=9)   # running page number at the foot
        doc.set_metadata({"title": title, "author": author})
        doc.save(path)
        doc.close()
    return first


def test_text_layer_and_links():
    from roughreader import documents as D
    paras = "".join(f'<p id="p{i}">Paragraph {i}. ' + "Words here and there. " * 12 + (f'<a href="#note{i}">[{i}]</a>' if i < 3 else "") + "</p>" for i in range(40))
    notes = "".join(f'<p id="note{i}">Note {i}: the source.</p>' for i in range(3))
    with tempfile.TemporaryDirectory() as d:
        epub = os.path.join(d, "a.epub")
        open(epub, "wb").write(D.html_to_epub(paras + "<h2>Notes</h2>" + notes, title="Linked", author="L"))
        book = D.open_book(epub)
        book.configure(500, 600)
        layer = book.text_layer(0)
        assert layer.unit == 0 and layer.first == 0 and layer.words and all(0 <= w[0] < w[2] <= 1 and 0 <= w[1] < w[3] <= 1 for w in layer.words)
        assert layer.text(0, 1) == "Paragraph 0."
        links = [l for l in layer.links if not isinstance(l[4], str)]
        assert links and links[0][4][0] == len(book) - 1, links[:1]   # the note is on the last page
        second = book.text_layer(1)
        assert second.first == len(layer.words) and book.page_of_word(0, second.first + 2) == 1
        word = second.first + 5
        book.configure(300, 600)                                         # narrower: more pages, same words
        page = book.page_of_word(0, word)
        later = book.text_layer(page)
        assert later.first <= word < later.first + len(later.words) and page >= 1
        book.close()
        pdf = os.path.join(d, "b.pdf")
        start = make_book_pdf(pdf)
        fixed = D.open_book(pdf)
        layer = fixed.text_layer(start)
        assert layer.unit == start and layer.first == 0 and "Beginning" in layer.text()
        assert fixed.page_of_word(start + 3, 10) == start + 3
        fixed.close()


def test_annotations():
    from roughreader import annotations as A
    with tempfile.TemporaryDirectory() as d:
        notes = A.Notes(d, "abc", "x.epub", "X")
        notes.add_highlight((2, 10), (2, 20), "first")
        notes.add_highlight((2, 18), (3, 4), "second")                   # overlaps: merged
        assert len(notes.highlights) == 1 and notes.highlights[0]["start"] == [2, 10] and notes.highlights[0]["end"] == [3, 4]
        notes.add_highlight((5, 0), (5, 3), "other", "blue")
        assert notes.highlight_at((2, 15)) is notes.highlights[0] and notes.highlight_at((4, 0)) is None
        assert notes.highlights_in(2, 0, 30) == [(10, 30, "yellow")] and notes.highlights_in(3, 0, 9) == [(0, 4, "yellow")]
        assert notes.toggle_bookmark(7, 100, 180, "page start", 12) is True
        assert notes.bookmark_on(7, 90, 160) is not None and notes.bookmark_on(7, 0, 99) is None
        again = A.Notes(d, "abc")                                        # saved on disk, found by fingerprint
        assert len(again.highlights) == 2 and len(again.bookmarks) == 1 and again.bookmarks[0]["label"] == "page start"
        assert again.toggle_bookmark(7, 100, 180, "x") is False and not A.Notes(d, "abc").bookmarks
        again.remove_highlight(again.highlights[0])
        assert len(A.Notes(d, "abc").highlights) == 1


def test_contents_detection():
    from roughreader import contents as C, documents as D
    assert C.roman_value("xiv") == 14 and C.ROMAN.match("xxix") and not C.ROMAN.match("mix")
    words = [(0.1, 0.10, 0.3, 0.12, "Contents", 1), (0.1, 0.2, 0.2, 0.22, "Chapter", 2), (0.21, 0.2, 0.3, 0.22, "One", 2),
             (0.8, 0.201, 0.85, 0.221, "1", 9), (0.1, 0.3, 0.3, 0.32, "Another", 3), (0.31, 0.3, 0.4, 0.32, "Part", 3),
             (0.8, 0.3, 0.85, 0.32, "I41", 9)]
    entries, heading = C.page_entries(words)                             # titles and numbers in separate blocks
    assert heading and entries == [{"title": "Chapter One", "printed": "1"}, {"title": "Another Part", "printed": "141"}]
    with tempfile.TemporaryDirectory() as d:
        pdf = os.path.join(d, "c.pdf")
        start = make_book_pdf(pdf)
        book = D.open_book(pdf)
        found = C.detect(book)
        assert [e["title"] for e in found["entries"]] == ["The Beginning", "The Middle Part", "The End"], found
        assert [e["page"] for e in found["entries"]] == [start, start + 10, start + 20], found
        assert found["method"] == "printed page numbers" and found["offsets"]["arabic"] == start - 1
        fixed = C.with_offset(found, book, start)                        # 'this page is printed page N', one off
        assert [e["page"] for e in fixed["entries"]] == [start + 1, start + 11, start + 21] and fixed["method"] == "set by hand"
        book.close()
        plain = os.path.join(d, "n.pdf")
        make_book_pdf(plain, contents=False)
        book = D.open_book(plain)
        assert C.detect(book)["entries"] == []
        book.close()


def test_analyze_and_rename():
    from roughreader import analyze as A, documents as D, organize as O
    assert A.isbn_valid("978-0-14-310825-2") and A.isbn_valid("0865274452") and not A.isbn_valid("9780143108253")
    assert A.to_isbn13("0-86527-445-2") == "9780865274457"
    assert A.find_isbns("blah ISBN 0-86527445-2 (pbk.) and 9780143108252") == ["9780865274457", "9780143108252"]
    assert A.edition_year("First published in German 1920. This edition published 2016 by Penguin.") == ("2016", "1920")
    assert A.find_publisher("Published by Howard Fertig, Inc. 80 East 11th Street") == "Howard Fertig"
    assert A.find_publisher("Copyright © University of Toronto Press 2016") == "University of Toronto Press"
    assert A.find_edition("FIRST PAPERBACK EDITION 1993") == "First Paperback Edition"
    assert A.find_translators("Translated from the German by Michael Hofmann. Foreword") == ["Michael Hofmann"]
    credits = ("© Scala. Reprinted by permission. © Alamy. Reproduced with permission. © Bridgeman.", False)
    legal = ("Copyright © 2016 Example Press. All rights reserved. ISBN 978-0-14-310825-2", False)
    assert A.copyright_text([credits, legal])[0].startswith("Copyright")
    details = A.Details(title="Storm of Steel", authors=["Ernst Jünger"], publisher="Penguin Books", year="2016")
    assert A.file_name(details, ".pdf") == "Storm of Steel, Jünger, Penguin Books, 2016.pdf"
    assert A.file_name(A.Details(title="Notes"), ".txt") == "Notes.txt"
    assert A.file_name(A.Details(title="Vol v01", series="Vol"), ".cbz", "1080p", "comic") == "Vol v01 - 1080p.cbz"
    with tempfile.TemporaryDirectory() as d:
        src, root = os.path.join(d, "src"), os.path.join(d, "Library")
        os.makedirs(src)
        make_book_pdf(os.path.join(src, "old book scan final.pdf"), title="Old Book", author="Ann Example")
        index = O.Index(root)
        plan = O.plan_import([src], root, index)
        O.run_import(plan, index)
        index = O.Index(root)
        todo = A.pending(root, index)
        assert len(todo) == 1
        moved = A.run(root, todo, use_ocr=False)
        assert len(moved) == 1
        new = moved[0][1]
        assert os.path.basename(new) == "Old Book, Example, Example Press, 2004.pdf", os.path.basename(new)
        assert os.path.basename(os.path.dirname(os.path.dirname(new))) == "Ann Example"
        saved = O.Index(root).entry(new)
        info = saved["details"]
        assert info["isbn"] == "9780143108252" and info["original_year"] == "1931" and saved["source"].endswith("old book scan final.pdf")
        assert A.pending(root, O.Index(root)) == []
        # a correction by hand: kept, and never overwritten by a newer automatic reading
        fixed = A.Details.from_dict(info)
        fixed.title, fixed.manual = "Old Book Revisited", True
        index = O.Index(root)
        newer = A.apply(root, new, fixed, index)
        index.save()
        assert os.path.basename(newer).startswith("Old Book Revisited, Example") and not os.path.exists(os.path.dirname(new))
        assert O.scan(root)[0].works[0].title == "Old Book Revisited"
        assert A.pending(root, O.Index(root)) == []
        assert O.scan(root)[0].works[0].variants[0].details["publisher"] == "Example Press"


def test_first_run():
    from roughreader import analyze, documents as D, firstrun, organize as O
    with tempfile.TemporaryDirectory() as d:
        data, cache, root = os.path.join(d, "data"), os.path.join(d, "data", "cache", "covers"), os.path.join(d, "Library")
        firstrun.ensure_folders(data, cache, root)
        assert all(os.path.isdir(f) for f in (data, os.path.join(data, "annotations"), cache, root))
        s = store.Store(data)
        manual = firstrun.install_manual(root, s)
        assert os.path.isfile(manual) and store.Store(data).get("manual") == firstrun.MANUAL_VERSION
        assert [a.name for a in O.scan(root)] == ["RoughReader"] and analyze.pending(root, O.Index(root)) == []
        meta = D.read_metadata(manual)
        assert (meta.title, meta.author) == ("RoughReader Manual", "RoughReader")
        book = D.open_book(manual)
        book.configure(600, 800)
        assert len(book) > 5 and "title:" not in book.text_layer(0).text() and book.toc()[0][1] == "RoughReader Manual"
        book.close()
        os.remove(manual)                                               # deleted by the reader: stays deleted
        assert firstrun.install_manual(root, store.Store(data)) == "" and not os.path.exists(manual)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
