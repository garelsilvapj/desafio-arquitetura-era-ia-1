import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coupling  # noqa: E402


def make(tmp_path, files):
    root = tmp_path / "helpdesk"
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return root


def rows(root):
    return {r["component"]: r for r in coupling.measure(root)}


def test_relative_and_absolute_imports(tmp_path):
    root = make(tmp_path, {
        "__init__.py": "", "sub/__init__.py": "",
        "a.py": "", "b.py": "", "x.py": "class Y: pass",
        "sub/mod.py": "", "sub/deep.py": "from ..a import foo\nfrom .. import b\n",
        "m1.py": "from . import a, b\nfrom .x import Y\n",
        "m2.py": "import helpdesk.x\nfrom helpdesk import a\nfrom helpdesk.sub import mod\n",
        "sub/m3.py": "from . import mod\nfrom ..x import Y\n",
    })
    r = rows(root)
    assert set(r) == {"a", "b", "x", "sub.mod", "sub.deep", "m1", "m2", "sub.m3"}
    assert r["m1"]["ce"] == 3
    assert r["m2"]["ce"] == 3
    assert r["sub.deep"]["ce"] == 2
    assert r["sub.m3"]["ce"] == 2
    assert r["a"]["ca"] == 3  # m1, m2, sub.deep
    assert r["sub.mod"]["ca"] == 2


def test_init_excluded_and_externals_ignored(tmp_path):
    root = make(tmp_path, {
        "__init__.py": "from . import a", "a.py": "import os, json\nimport requests\nfrom typing import Protocol\n",
    })
    r = rows(root)
    assert list(r) == ["a"]
    assert r["a"]["ce"] == 0 and r["a"]["ca"] == 0 and r["a"]["i"] == 0


def test_self_import_and_distinct(tmp_path):
    root = make(tmp_path, {
        "a.py": "from . import a\n", "b.py": "from .a import X\nfrom .a import Y\nimport helpdesk.a\n",
    })
    r = rows(root)
    assert r["a"]["ce"] == 0 and r["a"]["ca"] == 1
    assert r["b"]["ce"] == 1
    assert r["b"]["i"] == 1.0


def test_import_inside_function(tmp_path):
    root = make(tmp_path, {"a.py": "", "b.py": "def f():\n    from . import a\n"})
    assert rows(root)["b"]["ce"] == 1


def test_abstractness(tmp_path):
    root = make(tmp_path, {
        "p.py": ("import typing, abc\nfrom typing import Protocol, Generic, TypeVar\n"
                 "from abc import ABC\nimport typing_extensions\n"
                 "class A1(Protocol): ...\nclass A2(ABC): ...\nclass A3(Protocol[int]): ...\n"
                 "class A4(typing.Protocol): ...\nclass A5(abc.ABC): ...\n"
                 "class A6(typing_extensions.Protocol): ...\n"
                 "class C1: ...\nclass C2(A1): ...\n"),
        "none.py": "x = 1\n",
    })
    r = rows(root)
    assert r["p"]["a"] == 6 / 8
    assert r["none"]["a"] == 0
    # D com A=0.75, I=0
    assert r["p"]["d"] == 0.25


def test_zero_rules_and_d(tmp_path):
    root = make(tmp_path, {"iso.py": "class K: ..."})
    r = rows(root)["iso"]
    assert r["i"] == 0 and r["a"] == 0 and r["d"] == 1


def test_csv_and_cli(tmp_path):
    root = make(tmp_path, {"a.py": "", "b.py": "from . import a\n"})
    out = tmp_path / "out"
    assert coupling.main([str(root), "--name", "t", "--out-dir", str(out)]) == 0
    text = (out / "t.csv").read_bytes().decode()
    assert text == "component,ca,ce,i,a,d\na,1,0,0.00,0.00,1.00\nb,0,1,1.00,0.00,0.00\n"
    import xml.dom.minidom
    xml.dom.minidom.parse(str(out / "t.svg"))


def test_package_override(tmp_path):
    root = make(tmp_path, {"a.py": "", "b.py": "import other.a\n"})
    r = {x["component"]: x for x in coupling.measure(root, "other")}
    assert r["b"]["ce"] == 1


def test_svg_labels_do_not_overlap():
    placed = [(100 - 6, 100 - 6, 106, 106), (130 - 6, 100 - 6, 136, 106)]
    boxes = []
    for cx, c in ((100, "classification"), (100, "extraction"), (130, "report")):
        x, y, anc, _ = coupling.place_label(c, cx, 100, placed)
        b = coupling.label_box(x, y, anc, c)
        assert not any(coupling._overlap(b, p) for p in placed)
        placed.append(b)
        boxes.append(b)
