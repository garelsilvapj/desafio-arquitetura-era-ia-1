#!/usr/bin/env python3
"""Métricas de acoplamento (Ca, Ce, I, A, D) de um pacote Python.

Régua fixa:
- Componente = cada módulo .py dentro da pasta medida (inclui subpastas),
  identificado pelo caminho pontuado relativo à pasta; `__init__.py` é excluído.
- Dependência = import (relativo ou absoluto) que resolve para OUTRO componente.
  Bibliotecas externas/stdlib e auto-imports não contam. Imports em qualquer
  ponto do módulo contam (ast.walk).
- `from M import n`: se `M.n` é componente -> dependência em `M.n`; senão, se
  `M` é componente -> dependência em `M`; senão nada.
- Imports absolutos só valem se começam com o nome do pacote (basename da
  pasta medida, sobrescrevível por --package).
- Ca = nº de componentes DISTINTOS que importam este; Ce = nº de componentes
  DISTINTOS que este importa. I = Ce / (Ce + Ca), ou 0 se ambos forem 0.
- A = classes abstratas / classes (0 se não há classes). Abstrata = herda
  diretamente de Protocol ou ABC (também typing.Protocol, abc.ABC, Protocol[T]).
- D = |A + I - 1|. Precisão total; arredonda só na saída (2 casas).

Uso:
    python3 metrics/coupling.py <path/to/helpdesk> --name <nome>
        [--out-dir metrics/results] [--package helpdesk]
Gera <out-dir>/<nome>.csv e <out-dir>/<nome>.svg (somente biblioteca padrão).
"""
from __future__ import annotations

import argparse
import ast
import csv
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ABSTRACT_BASES = {"Protocol", "ABC", "typing.Protocol",
                  "typing_extensions.Protocol", "abc.ABC"}


def discover(root: Path) -> dict[str, Path]:
    comps = {}
    for p in sorted(root.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        rel = p.relative_to(root).with_suffix("")
        comps[".".join(rel.parts)] = p
    return comps


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def abstractness(tree: ast.AST) -> float:
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not classes:
        return 0.0
    abstract = sum(1 for c in classes if any(_dotted(b) in ABSTRACT_BASES for b in c.bases))
    return abstract / len(classes)


def imports_of(name: str, tree: ast.AST, comps: set[str], package: str) -> set[str]:
    pkg_parts = name.split(".")[:-1]  # pacote do módulo (relativo à pasta medida)
    deps: set[str] = set()

    def resolve_from(mod: str, names: list[str]) -> None:
        for n in names:
            full = f"{mod}.{n}" if mod else n
            if full in comps:
                deps.add(full)
            elif mod in comps:
                deps.add(mod)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if parts[0] != package:
                    continue
                mod = ".".join(parts[1:])
                if mod in comps:
                    deps.add(mod)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                parts = (node.module or "").split(".")
                if parts[0] != package:
                    continue
                mod = ".".join(parts[1:])
            else:
                up = node.level - 1
                if up > len(pkg_parts):
                    continue
                base = pkg_parts[:len(pkg_parts) - up]
                mod = ".".join(base + ([node.module] if node.module else []))
            resolve_from(mod, [a.name for a in node.names])
    deps.discard(name)
    return deps


def measure(root: Path, package: str | None = None) -> list[dict]:
    package = package or root.resolve().name
    files = discover(root)
    names = set(files)
    trees = {n: ast.parse(p.read_text(encoding="utf-8"), filename=str(p)) for n, p in files.items()}
    out_deps = {n: imports_of(n, trees[n], names, package) for n in files}
    rows = []
    for n in sorted(files):
        ca = sum(1 for m in files if n in out_deps[m])
        ce = len(out_deps[n])
        i = ce / (ce + ca) if ce + ca else 0.0
        a = abstractness(trees[n])
        rows.append({"component": n, "ca": ca, "ce": ce, "i": i, "a": a, "d": abs(a + i - 1)})
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["component", "ca", "ce", "i", "a", "d"])
        for r in rows:
            w.writerow([r["component"], r["ca"], r["ce"],
                        f"{r['i']:.2f}", f"{r['a']:.2f}", f"{r['d']:.2f}"])


CHAR_W, LINE_H = 6.6, 13
PAIN_TXT, USELESS_TXT = "Zona de dor (pain)", "Zona de inutilidade"


def label_box(x: float, y: float, anchor: str, text: str) -> tuple[float, float, float, float]:
    w = CHAR_W * len(text)
    x0 = {"start": x, "end": x - w, "middle": x - w / 2}[anchor]
    return (x0, y - 10, x0 + w, y + 3)


def caption_box(x, y, anchor, text):
    w = 0.9 * CHAR_W * len(text)
    x0 = x if anchor == "start" else x - w
    return (x0, y - 10, x0 + w, y + 3)


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place_label(text, cx, cy, placed, bounds=None):
    """Escolhe a 1a posição sem colisão; devolve (x, y, anchor, deslocado)."""
    cands = [(cx + 9, cy + 4, "start"), (cx - 9, cy + 4, "end"),
             (cx, cy - 10, "middle"), (cx, cy + 19, "middle")]
    for k in range(1, 8):
        for sgn in (-1, 1):
            cands.append((cx + 9, cy + 4 + sgn * LINE_H * k, "start"))
            cands.append((cx - 9, cy + 4 + sgn * LINE_H * k, "end"))
    for n, (x, y, anc) in enumerate(cands):
        box = label_box(x, y, anc, text)
        if bounds and (box[0] < bounds[0] or box[2] > bounds[2]
                       or box[1] < bounds[1] or box[3] > bounds[3]):
            continue
        if not any(_overlap(box, p) for p in placed):
            return x, y, anc, n >= 4
    x, y, anc = cands[0]
    return x, y, anc, False


def write_svg(rows: list[dict], name: str, path: Path) -> None:
    W, H = 760, 620
    L, R, T, B = 80, 40, 60, 80
    pw, ph = W - L - R, H - T - B
    X = lambda v: L + v * pw
    Y = lambda v: T + (1 - v) * ph
    pain_xy = (X(0.02), Y(0.47) + 4)
    useless_xy = (X(0.98), Y(0.98) + 12)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         'font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<text x="{W/2}" y="32" text-anchor="middle" font-size="18" font-weight="bold" fill="#111">'
         f'{escape(name)} — A × I (Main Sequence)</text>',
         # zonas
         f'<rect x="{X(0)}" y="{Y(0.5)}" width="{pw*0.5}" height="{ph*0.5}" fill="#e57373" fill-opacity="0.18"/>',
         f'<rect x="{X(0.5)}" y="{Y(1)}" width="{pw*0.5}" height="{ph*0.5}" fill="#ffb74d" fill-opacity="0.18"/>',
         f'<text x="{pain_xy[0]}" y="{pain_xy[1]}" font-size="11" fill="#b71c1c" '
         f'fill-opacity="0.7">{PAIN_TXT}</text>',
         f'<text x="{useless_xy[0]}" y="{useless_xy[1]}" text-anchor="end" font-size="11" '
         f'fill="#e65100" fill-opacity="0.7">{USELESS_TXT}</text>']
    for t in (0, 0.25, 0.5, 0.75, 1):
        s.append(f'<line x1="{X(t)}" y1="{Y(0)}" x2="{X(t)}" y2="{Y(1)}" stroke="#ddd"/>')
        s.append(f'<line x1="{X(0)}" y1="{Y(t)}" x2="{X(1)}" y2="{Y(t)}" stroke="#ddd"/>')
        s.append(f'<text x="{X(t)}" y="{Y(0)+18}" text-anchor="middle" font-size="12" fill="#333">{t:g}</text>')
        s.append(f'<text x="{X(0)-8}" y="{Y(t)+4}" text-anchor="end" font-size="12" fill="#333">{t:g}</text>')
    s.append(f'<rect x="{X(0)}" y="{Y(1)}" width="{pw}" height="{ph}" fill="none" stroke="#333"/>')
    s.append(f'<line x1="{X(0)}" y1="{Y(1)}" x2="{X(1)}" y2="{Y(0)}" stroke="#1565c0" '
             'stroke-width="2" stroke-dasharray="6 4"/>')
    s.append(f'<text x="{X(0.5)+6}" y="{Y(0.5)-8}" font-size="12" fill="#1565c0" '
             f'transform="rotate(45 {X(0.5)+6} {Y(0.5)-8})">Main Sequence</text>')
    s.append(f'<text x="{X(0.5)}" y="{H-30}" text-anchor="middle" font-size="14" fill="#111">'
             'I — Instabilidade (Ce / (Ce + Ca))</text>')
    s.append(f'<text x="22" y="{Y(0.5)}" text-anchor="middle" font-size="14" fill="#111" '
             f'transform="rotate(-90 22 {Y(0.5)})">A — Abstração</text>')
    groups: dict[tuple[float, float], list[str]] = {}
    for r in rows:
        groups.setdefault((round(r["i"], 6), round(r["a"], 6)), []).append(r["component"])
    pts = [(X(i), Y(a)) for (i, a) in groups]
    placed: list[tuple[float, float, float, float]] = [
        (cx - 6, cy - 6, cx + 6, cy + 6) for cx, cy in pts]
    placed += [caption_box(pain_xy[0], pain_xy[1], "start", PAIN_TXT),
               caption_box(useless_xy[0], useless_xy[1], "end", USELESS_TXT)]
    for cx, cy in pts:
        s.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="#2e7d32" stroke="#fff"/>')
    bounds = (X(0), Y(1), X(1) + R - 4, Y(0))
    for (i, a), comps in groups.items():
        cx, cy = X(i), Y(a)
        for c in comps:
            x, y, anchor, moved = place_label(c, cx, cy, placed, bounds)
            box = label_box(x, y, anchor, c)
            placed.append(box)
            if moved:
                ex = min(max(cx, box[0]), box[2])
                ey = min(max(cy, box[1]), box[3])
                s.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
                         'stroke="#888" stroke-width="0.8"/>')
            s.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="12" '
                     f'fill="#111">{escape(c)}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Métricas de acoplamento (Ca/Ce/I/A/D)")
    ap.add_argument("path", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "results")
    ap.add_argument("--package", default=None)
    args = ap.parse_args(argv)
    if not args.path.is_dir():
        print(f"pasta não encontrada: {args.path}", file=sys.stderr)
        return 2
    rows = measure(args.path, args.package)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, args.out_dir / f"{args.name}.csv")
    write_svg(rows, args.name, args.out_dir / f"{args.name}.svg")
    w = max([len(r["component"]) for r in rows] + [9])
    print(f"{'component':<{w}}  ca  ce     i     a     d")
    for r in rows:
        print(f"{r['component']:<{w}}  {r['ca']:>2}  {r['ce']:>2}  {r['i']:.2f}  {r['a']:.2f}  {r['d']:.2f}")
    pain = [r["component"] for r in rows if r["a"] < 0.5 and r["i"] < 0.5 and r["d"] >= 0.5]
    print("\nZona de dor (A<0.5, I<0.5, D>=0.5): " + (", ".join(pain) if pain else "nenhum"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
