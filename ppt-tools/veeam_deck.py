#!/usr/bin/env python3
"""Shared toolkit for building blueprint concept decks in the official Veeam style.

This is the canonical copy — do not duplicate it into a blueprint directory. Each
blueprint keeps only its *content*, in `<blueprint-dir>/ppt-tools/deck.py`, which
imports this module and calls the slide builders below.

    from veeam_deck import Deck
    d = Deck('cnpg', 'cnpg-example')
    d.title("CNPG BACKUP WITH KASTEN", "Cold backup through the operator's own API")
    d.agenda([...])
    d.cards("WHAT THIS PROTECTS", "subtitle", [...])
    d.save()

Everything the deck needs — dark backgrounds, the Veeam logo, the theme, the fonts
and the SVG icons — comes from the bundled `veeam-pptx` skill. Never rebuild a dark
slide from scratch; use the template layouts.

Text is auto-fitted: each text box is measured against the real ES Build Neutral
metrics and the font size steps down until the text fits, so a long README sentence
degrades gracefully instead of silently clipping. Anything that still does not fit
is reported at the end of the build.

See README.md in this directory for the full authoring guide.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ── The veeam-pptx skill bundle (template, fonts, icons) ────────────────────
SKILL_DIR = Path(os.environ.get(
    'VEEAM_PPTX_SKILL', Path.home() / '.claude/skills/veeam-pptx')).expanduser()
ASSETS = SKILL_DIR / 'assets'
ICONS_DIR = ASSETS / 'icons'
FONTS_DIR = ASSETS / 'fonts'
TEMPLATE = ASSETS / 'Pitch_deck_template.pptx'
REPO_ROOT = Path(__file__).resolve().parent.parent

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
ASVG = 'http://schemas.microsoft.com/office/drawing/2016/SVG/main'
FONT = 'ES Build Neutral'

# Brand palette (references/brand.md)
NAVY = '002060'      # titles on light slides
INK = '1D1F2A'       # body on light slides
GREY = '505861'      # secondary body — light slides ONLY, never on dark
MUTED = '929BA5'     # subtitles on light slides
GREEN = '00D15F'
BLUE = '01B0FE'
RED = 'ED2B3D'
ORANGE = 'FE8A25'
WHITE = 'FFFFFF'
BORDER = 'DBDEE1'
PANEL = 'F2F3F2'
TINT = 'EAF9FF'
DEEP_GREEN = '00703C'

GRAD_POS_DARK = [('4AFF9C', 4), ('67FFFE', 80)]
GRAD_NEG_DARK = [('FFD839', 4), ('ED2B3D', 80)]

# Content zone — Critical Rule 0. Nothing may be placed outside it.
CX, CY = 0.5158, 0.3494
CW, CH = 12.3017, 6.5422
CRIGHT, CBOTTOM = CX + CW, CY + CH

DARK_LAYOUT = 'Quote Slide Dark 2'
LIGHT_LAYOUT = 'Content Slide 3'

TITLE_SIZE = 28          # Critical Rule 13 — every content-slide title, light or dark
SUBTITLE_SIZE = 14


# ── Font install (local render only; the .pptx does not embed fonts) ────────
def install_fonts() -> None:
    if sys.platform == 'darwin':
        dst, refresh = Path('~/Library/Fonts').expanduser(), None
    elif sys.platform.startswith('linux'):
        dst, refresh = Path('~/.local/share/fonts').expanduser(), ['fc-cache', '-f']
    else:
        return
    dst.mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob('*.ttf'):
        if not (dst / f.name).exists():
            shutil.copy(f, dst / f.name)
    if refresh and shutil.which(refresh[0]):
        subprocess.run(refresh, capture_output=True)


# ── Text measurement, for auto-fit ─────────────────────────────────────────
_REG = FONTS_DIR / 'ESBuildNeutral-Regular.ttf'
_BOLD = FONTS_DIR / 'ESBuildNeutral-Bold.ttf'
_FCACHE: dict = {}


def _font(size_pt: float, bold: bool):
    key = (round(size_pt * 2), bold)
    if key not in _FCACHE:
        path = _BOLD if bold else _REG
        _FCACHE[key] = ImageFont.truetype(str(path), max(1, int(round(size_pt * 2))))
    return _FCACHE[key]


def text_width_in(text: str, size_pt: float, bold: bool) -> float:
    """Width of `text` in inches, at `size_pt`, using the real brand font."""
    if not text:
        return 0.0
    return _font(size_pt, bold).getlength(text) / 2.0 / 72.0


def wrapped_lines(text: str, avail_in: float, size_pt: float, bold: bool) -> int:
    """Greedy word-wrap line count for one paragraph."""
    if not text.strip():
        return 1
    if avail_in <= 0:
        return 1
    if text_width_in(text, size_pt, bold) <= avail_in:
        return 1
    lines, cur = 1, 0.0
    for word in text.split(' '):
        ww = text_width_in(word + ' ', size_pt, bold)
        if cur + ww > avail_in and cur > 0:
            lines += 1
            cur = ww
        else:
            cur += ww
    return lines


def text_height_in(text: str, w_in: float, size_pt: float, bold: bool,
                   line_spacing: float = 1.0, space_after_pt: float = 0.0) -> float:
    """Height the text needs, in inches, wrapped into a box `w_in` wide."""
    total = 0.0
    paras = text.split('\n')
    for i, para in enumerate(paras):
        n = wrapped_lines(para, w_in - 0.02, size_pt, bold)
        total += n * size_pt * 1.2 * max(1.0, line_spacing) / 72.0
        if space_after_pt and i < len(paras) - 1:
            total += space_after_pt / 72.0
    return total


def fit_size(text: str, w_in: float, h_in: float, size_pt: float, bold: bool,
             line_spacing: float = 1.0, space_after_pt: float = 0.0,
             min_size: float = 8.5) -> float:
    """Largest size <= `size_pt` at which the text fits the box. Steps by 0.5pt."""
    s = size_pt
    while s > min_size:
        if text_height_in(text, w_in, s, bold, line_spacing, space_after_pt) <= h_in:
            return s
        s -= 0.5
    return min_size


# ── Low-level shape helpers (canonical forms from the skill reference) ──────
def set_adj(shape, val):
    pg = shape._element.find(f'.//{{{A}}}prstGeom')
    if pg is None:
        return
    av = pg.find(f'{{{A}}}avLst')
    if av is None:
        av = etree.SubElement(pg, f'{{{A}}}avLst')
    for g in av.findall(f'{{{A}}}gd'):
        av.remove(g)
    g = etree.SubElement(av, f'{{{A}}}gd')
    g.set('name', 'adj')
    g.set('fmla', f'val {val}')


def set_solid_fill(shape, hex_color, alpha=100):
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(hex_color.lstrip('#'))
    if alpha < 100:
        sf = shape._element.spPr.find(f'{{{A}}}solidFill')
        if sf is not None:
            clr = sf.find(f'{{{A}}}srgbClr')
            if clr is not None:
                etree.SubElement(clr, f'{{{A}}}alpha').set('val', str(int(alpha * 1000)))


def set_grad_fill(shape, stops, angle_deg=45):
    spPr = shape._element.spPr
    for tag in ('solidFill', 'noFill', 'gradFill', 'blipFill', 'pattFill'):
        el = spPr.find(f'{{{A}}}{tag}')
        if el is not None:
            spPr.remove(el)
    gf = etree.Element(f'{{{A}}}gradFill')
    gsLst = etree.SubElement(gf, f'{{{A}}}gsLst')
    for hex_c, pos in stops:
        gs = etree.SubElement(gsLst, f'{{{A}}}gs')
        gs.set('pos', str(int(pos * 1000)))
        etree.SubElement(gs, f'{{{A}}}srgbClr').set('val', hex_c.upper().lstrip('#'))
    lin = etree.SubElement(gf, f'{{{A}}}lin')
    lin.set('ang', str(int(angle_deg * 60000)))
    lin.set('scaled', '0')
    idx = len(list(spPr))
    for i, child in enumerate(spPr):
        if child.tag.split('}')[-1] in ('ln', 'effectLst', 'scene3d', 'sp3d', 'extLst'):
            idx = i
            break
    spPr.insert(idx, gf)


def set_no_line(shape):
    shape.line.fill.background()


def set_line(shape, hex_color, width_pt=1):
    shape.line.color.rgb = RGBColor.from_string(hex_color.lstrip('#'))
    shape.line.width = Pt(width_pt)


def set_gradient_line(shape, stops, angle_deg=0):
    spPr = shape._element.spPr
    ln = spPr.find(f'{{{A}}}ln')
    if ln is None:
        ln = etree.SubElement(spPr, f'{{{A}}}ln')
    for ch in list(ln):
        ln.remove(ch)
    gf = etree.SubElement(ln, f'{{{A}}}gradFill')
    gsl = etree.SubElement(gf, f'{{{A}}}gsLst')
    for hex_c, pos in stops:
        gs = etree.SubElement(gsl, f'{{{A}}}gs')
        gs.set('pos', str(int(pos * 1000)))
        etree.SubElement(gs, f'{{{A}}}srgbClr').set('val', hex_c.lstrip('#').upper())
    li = etree.SubElement(gf, f'{{{A}}}lin')
    li.set('ang', str(int(angle_deg * 60000)))
    li.set('scaled', '0')


def add_shadow(shape, blur=15, color='000000', alpha=7, dist=0):
    """Always LAST — after every fill and line operation."""
    spPr = shape._element.spPr
    old = spPr.find(f'{{{A}}}effectLst')
    if old is not None:
        spPr.remove(old)
    el = etree.SubElement(spPr, f'{{{A}}}effectLst')
    sh = etree.SubElement(el, f'{{{A}}}outerShdw')
    sh.set('blurRad', str(int(blur * 12700)))
    sh.set('dist', str(int(dist * 12700)))
    for k, v in (('dir', '0'), ('sx', '102000'), ('sy', '102000'),
                 ('algn', 'ctr'), ('rotWithShape', '0')):
        sh.set(k, v)
    clr = etree.SubElement(sh, f'{{{A}}}srgbClr')
    clr.set('val', color.upper().lstrip('#'))
    if alpha < 100:
        etree.SubElement(clr, f'{{{A}}}alpha').set('val', str(int(alpha * 1000)))


def set_body_pr(shape, anchor='ctr', wrap=True, l_ins=0, r_ins=0, t_ins=0, b_ins=0):
    txBody = shape._element.find(f'.//{{{A}}}txBody')
    if txBody is None:
        return
    bodyPr = txBody.find(f'{{{A}}}bodyPr')
    if bodyPr is None:
        bodyPr = etree.SubElement(txBody, f'{{{A}}}bodyPr')
    bodyPr.set('anchor', anchor)
    bodyPr.set('wrap', 'square' if wrap else 'none')
    for k, v in (('lIns', l_ins), ('rIns', r_ins), ('tIns', t_ins), ('bIns', b_ins)):
        bodyPr.set(k, str(v))


def set_run_grad_fill(run, stops, angle_deg=0):
    """gradFill MUST be the first child of rPr, or PowerPoint renders grey."""
    rPr = run._r.get_or_add_rPr()
    for tag in ('solidFill', 'gradFill', 'noFill'):
        el = rPr.find(f'{{{A}}}{tag}')
        if el is not None:
            rPr.remove(el)
    gf = etree.Element(f'{{{A}}}gradFill')
    gsLst = etree.SubElement(gf, f'{{{A}}}gsLst')
    for hex_c, pos in stops:
        gs = etree.SubElement(gsLst, f'{{{A}}}gs')
        gs.set('pos', str(int(pos * 1000)))
        etree.SubElement(gs, f'{{{A}}}srgbClr').set('val', hex_c.upper().lstrip('#'))
    lin = etree.SubElement(gf, f'{{{A}}}lin')
    lin.set('ang', str(int(angle_deg * 60000)))
    lin.set('scaled', '0')
    rPr.insert(0, gf)


def add_run_glow(run, blur_pt=15, color='4AFF9C', alpha=40):
    """effectLst at position 1: gradFill(0) -> effectLst(1) -> latin(2)."""
    rPr = run._r.get_or_add_rPr()
    eff = rPr.find(f'{{{A}}}effectLst')
    if eff is not None:
        rPr.remove(eff)
    eff = etree.Element(f'{{{A}}}effectLst')
    shdw = etree.SubElement(eff, f'{{{A}}}outerShdw')
    shdw.set('blurRad', str(int(blur_pt * 12700)))
    for k, v in (('dist', '0'), ('dir', '0'), ('sx', '102000'), ('sy', '102000'),
                 ('algn', 'ctr'), ('rotWithShape', '0')):
        shdw.set(k, v)
    clr = etree.SubElement(shdw, f'{{{A}}}srgbClr')
    clr.set('val', color.upper().lstrip('#'))
    if alpha < 100:
        etree.SubElement(clr, f'{{{A}}}alpha').set('val', str(int(alpha * 1000)))
    rPr.insert(1, eff)


def remove_empty_placeholders(slide):
    spTree = slide.shapes._spTree
    for ph in list(slide.placeholders):
        tf = ph.text_frame
        if not any(r.text.strip() for p in tf.paragraphs for r in p.runs):
            try:
                spTree.remove(ph._element)
            except Exception:
                pass


# ── Icon plates ─────────────────────────────────────────────────────────────
_ICON_KW = json.load(open(ICONS_DIR / '_keywords.json', encoding='utf-8'))

ICON_TO_PLATE = {
    'cloud': 'blue', 'database': 'blue', 'server': 'blue', 'upload': 'blue',
    'lock': 'blue', 'shield': 'blue', 'global': 'blue', 'instance': 'blue',
    'node': 'blue', 'history': 'blue', 'loop': 'blue', 'scan': 'blue',
    'speed': 'blue', 'settings': 'blue', 'file': 'blue', 'folder': 'blue',
    'time': 'blue', 'contract': 'blue', 'search': 'blue', 'window': 'blue',
    'bar_chart': 'blue', 'line_chart': 'blue', 'pie_chart': 'blue', 'user': 'blue',
    'building': 'blue', 'calendar': 'blue', 'message': 'blue', 'money': 'blue',
    'currency': 'blue', 'question': 'blue', 'device': 'blue', 'function': 'blue',
    'equalizer': 'blue', 'macbook': 'blue', 'government': 'blue', 'hospital': 'blue',
    'organization': 'blue', 'customer': 'blue', 'handshake': 'blue',
    'sparkling': 'green', 'checkbox': 'green', 'rocket': 'green', 'trophy': 'green',
    'check': 'green', 'service': 'green',
    'virus': 'orange', 'spam': 'orange', 'error': 'orange', 'close': 'orange',
    'brain': 'purple', 'robot': 'purple', 'chat': 'purple', 'spy': 'purple',
    'microsoft': 'purple',
}

PLATE_CFG = {
    'blue':        dict(adj=21716, fill='CCEFFF', shadow=('99DFFF', 64), icon='#01B0FE'),
    'green':       dict(adj=21716, grad=[('00FF47', 0), ('01B0FE', 100)], ang=45,
                        shadow=('5AE88F', 100), icon='#FFFFFF'),
    'orange':      dict(adj=21716, fill='FFE8D3', shadow=('FEB97C', 40), icon='#FE8A25'),
    'purple':      dict(adj=21716, fill='DDD9FF', shadow=('A89CFF', 70), icon='#8E71F4'),
    'dark_teal':   dict(adj=25207, fill='FFFFFF', alpha=8,
                        border=[('4AFF9C', 0), ('67FFFE', 100)],
                        shadow=('67FFFE', 100), icon='#4AFF9C'),
    'dark_green':  dict(adj=21716, grad=[('00FF47', 0), ('01B0FE', 100)], ang=45,
                        shadow=('5AE88F', 100), icon='#FFFFFF'),
    'dark_orange': dict(adj=25207, fill='FFFFFF', alpha=8,
                        border=[('FFD839', 0), ('ED2B3D', 100)],
                        shadow=('FFD839', 100), icon='#FFD839'),
    'dark_purple': dict(adj=22769, fill='FFFFFF', alpha=8,
                        border=[('F494FF', 0), ('7033CC', 100)],
                        shadow=('9980FF', 100), icon='#F494FF'),
}


def find_svg_icon(keyword: str) -> str:
    """Keyword lookup over the 65 bundled generic icons. Never returns None —
    falls back to the checkmark, so an icon slot is never empty."""
    kw = keyword.lower()
    for slug in sorted(_ICON_KW):
        if kw in _ICON_KW[slug]:
            return str(ICONS_DIR / slug)
    return str(ICONS_DIR / 'check.svg')


def colorize_svg(svg_path: str, color_hex: str) -> bytes:
    svg = open(svg_path).read()
    svg = svg.replace('fill="currentColor"', f'fill="{color_hex}"')
    svg = re.sub(r'fill="#[0-9a-fA-F]+"', f'fill="{color_hex}"', svg)
    return svg.encode('utf-8')


def _png_visible(png_bytes: bytes, min_frac=0.002) -> bool:
    try:
        im = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
        hist = im.getchannel('A').histogram()
        return sum(hist[17:]) >= max(4, int(im.width * im.height * min_frac))
    except Exception:
        return False


def plate_variant(keyword: str, dark=False) -> str:
    base = 'blue'
    for k, v in ICON_TO_PLATE.items():
        if k in keyword.lower():
            base = v
            break
    if dark:
        return {'blue': 'dark_teal', 'green': 'dark_green',
                'orange': 'dark_orange', 'purple': 'dark_purple'}.get(base, 'dark_teal')
    return base


# ── The Deck ────────────────────────────────────────────────────────────────
class Deck:
    """A blueprint concept deck.

    `slug` names the output file, `blueprint_dir` is the blueprint directory
    relative to the repo root. The deck is written to
    `<repo>/<blueprint_dir>/<slug>.pptx`.
    """

    def __init__(self, slug: str, blueprint_dir: str, install_font=True):
        if not TEMPLATE.exists():
            sys.exit(f"Veeam template not found at {TEMPLATE}\n"
                     f"Set VEEAM_PPTX_SKILL to the veeam-pptx skill directory.")
        if install_font:
            install_fonts()
        self.slug = slug
        self.out = str(REPO_ROOT / blueprint_dir / f'{slug}.pptx')
        # The bundled template is read-only; copy the bytes, not the mode, or the
        # next rebuild cannot overwrite its own output.
        shutil.copyfile(TEMPLATE, self.out)
        os.chmod(self.out, 0o644)
        self.prs = Presentation(self.out)
        self._delete_all_slides()
        self._icons: list = []       # (slide, rId, keyword, colour)
        self._png_n = 0
        self._warnings: list = []

    # ---- plumbing --------------------------------------------------------
    def _delete_all_slides(self):
        lst = self.prs.slides._sldIdLst
        for sldId in list(lst):
            rId = sldId.get(qn('r:id'))
            lst.remove(sldId)
            try:
                self.prs.part.drop_rel(rId)
            except Exception:
                pass

    def _layout(self, fragment):
        layouts = list(self.prs.slide_masters[0].slide_layouts)
        for l in layouts:
            if l.name == fragment:
                return l
        for l in layouts:
            if fragment in l.name:
                return l
        raise ValueError(f"Layout not found: {fragment}")

    def _new(self, layout_name):
        s = self.prs.slides.add_slide(self._layout(layout_name))
        remove_empty_placeholders(s)
        return s

    def warn(self, msg):
        self._warnings.append(f"[{self.slug}] {msg}")

    # ---- primitives ------------------------------------------------------
    def tb(self, slide, x, y, w, h, text, size=14, bold=False, color=WHITE,
           align=PP_ALIGN.LEFT, wrap=True, anchor='t', line_spacing=1.0,
           space_after=0, autofit=True, min_size=8.5):
        """Textbox. '\\n' starts a new paragraph. Auto-shrinks to fit the box."""
        if autofit and wrap:
            size = fit_size(text, w, h, size, bold, line_spacing, space_after, min_size)
            need = text_height_in(text, w, size, bold, line_spacing, space_after)
            if need > h + 0.05:
                self.warn(f"text still overflows at {size}pt "
                          f"(needs {need:.2f}\", box {h:.2f}\"): {text[:60]!r}")
        elif autofit and not wrap:
            while size > min_size and text_width_in(max(text.split('\n'), key=len),
                                                    size, bold) > w:
                size -= 0.5
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = wrap
        set_body_pr(box, anchor=anchor, wrap=wrap, l_ins=0, r_ins=0, t_ins=0, b_ins=0)
        for i, line in enumerate(text.split('\n')):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            if line_spacing > 1.0 or space_after:
                pPr = p._p.get_or_add_pPr()
                if line_spacing > 1.0:
                    ln = etree.Element(f'{{{A}}}lnSpc')
                    etree.SubElement(ln, f'{{{A}}}spcPct').set(
                        'val', str(int(line_spacing * 100 * 1000)))
                    pPr.insert(0, ln)
                if space_after:
                    sa = etree.SubElement(pPr, f'{{{A}}}spcAft')
                    etree.SubElement(sa, f'{{{A}}}spcPts').set(
                        'val', str(int(space_after * 100)))
            r = p.add_run()
            r.text = line
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = RGBColor.from_string(color.lstrip('#'))
        return box

    def grad_text(self, slide, x, y, w, h, text, size, polarity='pos',
                  align=PP_ALIGN.CENTER):
        """A gradient-filled headline run, with the matching outer glow.
        Dark slides only — the gradient is defined for a dark background."""
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        box.text_frame.word_wrap = True
        p = box.text_frame.paragraphs[0]
        p.alignment = align
        r = p.add_run()
        r.text = text
        r.font.name, r.font.bold, r.font.size = FONT, True, Pt(size)
        if polarity == 'pos':
            set_run_grad_fill(r, GRAD_POS_DARK, angle_deg=0)
            add_run_glow(r, blur_pt=15, color='4AFF9C', alpha=40)
        else:
            set_run_grad_fill(r, GRAD_NEG_DARK, angle_deg=0)
            add_run_glow(r, blur_pt=15, color='FFD836', alpha=25)
        return box

    def eyebrow(self, slide, x, y, w, text, dark=True):
        """Kicker above a title: green, bold, UPPERCASE, loose tracking."""
        box = self.tb(slide, x, y, w, 0.3, text.upper(), size=13, bold=True,
                      color='4AFF9C' if dark else GREEN, wrap=False)
        for p in box.text_frame.paragraphs:
            for r in p.runs:
                r._r.get_or_add_rPr().set('spc', '200')
        return box

    def rrect(self, slide, x, y, w, h, adj=4667, fill=WHITE, alpha=100,
              line=BORDER, grad_line=None, shadow=('000000', 7, 15)):
        sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                    Inches(x), Inches(y), Inches(w), Inches(h))
        set_adj(sh, adj)
        set_solid_fill(sh, fill, alpha=alpha)
        if grad_line:
            set_gradient_line(sh, grad_line)
        elif line:
            set_line(sh, line, 1)
        else:
            set_no_line(sh)
        if shadow:
            add_shadow(sh, blur=shadow[2], color=shadow[0], alpha=shadow[1])
        return sh

    def arrow(self, slide, x, y, w, h, color=BLUE):
        sh = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                                    Inches(x), Inches(y), Inches(w), Inches(h))
        set_solid_fill(sh, color)
        set_no_line(sh)
        return sh

    def pill(self, slide, x, y, w, label, h=0.36, polarity='pos'):
        sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                    Inches(x), Inches(y), Inches(w), Inches(h))
        set_adj(sh, 50000)
        set_solid_fill(sh, WHITE, alpha=8)
        if polarity == 'neg':
            set_gradient_line(sh, [('FFD839', 0), ('ED2B3D', 100)])
            add_shadow(sh, blur=15, color='FFD839', alpha=70)
        else:
            set_gradient_line(sh, [('4AFF9C', 0), ('67FFFE', 100)])
            add_shadow(sh, blur=15, color='67FFFE', alpha=70)
        set_body_pr(sh, anchor='ctr', wrap=False, l_ins=0, r_ins=0, t_ins=0, b_ins=0)
        p = sh.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = label
        size = 12.0
        while size > 8.5 and text_width_in(label, size, False) > w - 0.2:
            size -= 0.5
        r.font.name, r.font.size = FONT, Pt(size)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        return sh

    def pill_row(self, slide, y, labels, polarity='pos', h=0.36, gap=0.3):
        """Centre a row of pills, each sized to its own label."""
        widths = [max(1.5, text_width_in(l, 12, False) + 0.55) for l in labels]
        total = sum(widths) + gap * (len(labels) - 1)
        x = CX + (CW - total) / 2
        for label, w in zip(labels, widths):
            self.pill(slide, x, y, w, label, h=h, polarity=polarity)
            x += w + gap

    def icon_plate(self, slide, x, y, keyword, variant=None, dark=False, size=0.55):
        """Plate + icon. The icon and the plate always share a colour family, and
        the icon is registered for post-save SVG injection."""
        large = size > 0.9843                 # > 2.5 cm -> translucent plate style
        if variant is None:
            variant = plate_variant(keyword, dark or large)
        if (dark or large) and not variant.startswith('dark_'):
            variant = {'blue': 'dark_teal', 'green': 'dark_green',
                       'orange': 'dark_orange',
                       'purple': 'dark_purple'}.get(variant, 'dark_teal')
        cfg = dict(PLATE_CFG.get(variant, PLATE_CFG['blue']))
        if large and not dark and 'alpha' in cfg:
            cfg['icon'] = {'dark_teal': '#01B0FE', 'dark_orange': '#FE8A25',
                           'dark_purple': '#8E71F4'}.get(variant, cfg['icon'])

        plate = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                       Inches(x), Inches(y), Inches(size), Inches(size))
        set_adj(plate, cfg['adj'])
        if 'grad' in cfg:
            set_grad_fill(plate, cfg['grad'], angle_deg=cfg['ang'])
            set_no_line(plate)
        else:
            set_solid_fill(plate, cfg['fill'], alpha=cfg.get('alpha', 100))
            if 'border' in cfg:
                set_gradient_line(plate, cfg['border'])
            else:
                set_no_line(plate)
        add_shadow(plate, blur=15, color=cfg['shadow'][0], alpha=cfg['shadow'][1])

        pad = size * 0.22
        png = self._icon_png(keyword, cfg['icon'],
                             size=max(32, int((size - 2 * pad) * 200)))
        pic = slide.shapes.add_picture(io.BytesIO(png), Inches(x + pad), Inches(y + pad),
                                       Inches(size - 2 * pad), Inches(size - 2 * pad))
        blip = pic._element.find(f'.//{{{A}}}blip')
        if blip is not None:
            rid = blip.get('{http://schemas.openxmlformats.org/officeDocument/'
                           '2006/relationships}embed', '')
            self._icons.append((slide, int(rid.replace('rId', '')) if rid else 0,
                                keyword, cfg['icon']))
        return plate

    def _icon_png(self, keyword, color_hex, size=56):
        """Visible PNG stub (Critical Rule 16). Unique bytes per call, so
        python-pptx never deduplicates two icons onto a single rId."""
        self._png_n += 1
        colored = colorize_svg(find_svg_icon(keyword), color_hex)
        if shutil.which('rsvg-convert'):
            try:
                with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
                    f.write(colored)
                    tmp = f.name
                try:
                    res = subprocess.run(['rsvg-convert', '-w', str(size), '-h',
                                          str(size), '-f', 'png', tmp],
                                         capture_output=True, timeout=10)
                    if res.returncode == 0 and _png_visible(res.stdout):
                        return res.stdout
                finally:
                    os.unlink(tmp)
            except Exception:
                pass
        try:
            import cairosvg
            png = cairosvg.svg2png(bytestring=colored, output_width=size,
                                   output_height=size)
            if _png_visible(png):
                im = Image.open(io.BytesIO(png)).convert('RGBA')
                a0 = im.getpixel((0, 0))[3]
                im.putpixel((0, 0), ((self._png_n * 37) % 256, (self._png_n * 71) % 256,
                                     128, max(1, a0)))
                buf = io.BytesIO()
                im.save(buf, 'PNG')
                return buf.getvalue()
        except ImportError:
            pass
        # last resort: a visible tile, never a transparent stub
        r, g, b = (int(color_hex.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        m = max(2, size // 8)
        ImageDraw.Draw(img).rounded_rectangle([m, m, size - m, size - m],
                                              radius=max(3, size // 6),
                                              fill=(r, g, b, 255))
        img.putpixel((0, 0), ((self._png_n * 37) % 256, (self._png_n * 71) % 256,
                              128, 255))
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()

    def card(self, slide, x, y, w, h, icon, title, body_lines, style='standard',
             dark=False, body_size=11.5):
        """style: standard | highlight | negative (light slides) — ignored when dark."""
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                      Inches(x), Inches(y), Inches(w), Inches(h))
        set_adj(card, 4667)
        if dark:
            set_solid_fill(card, WHITE, alpha=6)
            set_gradient_line(card, [('4AFF9C', 0), ('67FFFE', 100)])
            add_shadow(card, blur=15, color='4AFF9C', alpha=30)
            title_c, body_c, variant = WHITE, WHITE, None
        elif style == 'highlight':
            set_solid_fill(card, TINT)
            set_line(card, '99DFFF', 1)
            add_shadow(card, blur=15, color='000000', alpha=7)
            title_c, body_c, variant = NAVY, GREY, None
        elif style == 'negative':
            set_solid_fill(card, WHITE)
            set_line(card, RED, 1)
            add_shadow(card, blur=15, color='000000', alpha=7)
            title_c, body_c, variant = RED, GREY, 'orange'
        else:
            set_solid_fill(card, WHITE)
            set_line(card, BORDER, 1)
            add_shadow(card, blur=15, color='000000', alpha=7)
            title_c, body_c, variant = NAVY, GREY, None

        self.icon_plate(slide, x + 0.2, y + 0.2, icon, variant=variant,
                        dark=dark, size=0.5)
        self.tb(slide, x + 0.82, y + 0.23, w - 1.02, 0.44, title, size=13, bold=True,
                color=title_c, line_spacing=1.0)
        body = '\n'.join(body_lines) if isinstance(body_lines, (list, tuple)) \
            else body_lines
        self.tb(slide, x + 0.2, y + 0.85, w - 0.4, h - 1.02, body, size=body_size,
                color=body_c, line_spacing=1.12, space_after=5)
        return card

    def table(self, slide, x, y, w, h, headers, rows, col_widths=None,
              row_heights=None, header_size=12.5, body_size=11,
              first_col_accent=True):
        """A Veeam-styled table: navy first header cell, green for the rest."""
        n_rows, n_cols = len(rows) + 1, len(headers)
        shape = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y),
                                       Inches(w), Inches(h))
        tbl = shape.table
        if col_widths:
            for ci, cwid in enumerate(col_widths):
                tbl.columns[ci].width = Inches(cwid)
        if row_heights:
            for ri, rh in enumerate(row_heights):
                tbl.rows[ri].height = Inches(rh)
        for ci, head in enumerate(headers):
            self._cell(tbl.cell(0, ci), head.upper(), size=header_size, bold=True,
                       color=WHITE, fill=NAVY if ci == 0 else None,
                       fill_scheme=None if ci == 0 else 'accent2', header=True)
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                if ci == 0 and first_col_accent:
                    self._cell(tbl.cell(ri + 1, ci), val, size=body_size + 1,
                               bold=True, color=DEEP_GREEN, fill=TINT)
                else:
                    self._cell(tbl.cell(ri + 1, ci), val, size=body_size, color=INK)
        self._finish_table(shape)
        return shape

    def _cell(self, cell, text, size=12, bold=False, color=INK, fill=None,
              fill_scheme=None, header=False, align=PP_ALIGN.LEFT):
        tc = cell._tc
        tcPr = tc.find(f'{{{A}}}tcPr')
        if tcPr is None:
            tcPr = etree.SubElement(tc, f'{{{A}}}tcPr')
        tcPr.set('anchor', 'ctr')
        tcPr.set('marL', '91440')
        tcPr.set('marR', '91440')
        for tag in ('lnL', 'lnR', 'lnT', 'lnB', 'solidFill', 'gradFill', 'noFill'):
            el = tcPr.find(f'{{{A}}}{tag}')
            if el is not None:
                tcPr.remove(el)
        if header:
            tcPr.append(_tc_ln('lnR', color_hex=NAVY))
            tcPr.append(_tc_ln('lnB', scheme='tx1'))
        if fill_scheme:
            sf = etree.SubElement(tcPr, f'{{{A}}}solidFill')
            etree.SubElement(sf, f'{{{A}}}schemeClr').set('val', fill_scheme)
        elif fill:
            sf = etree.SubElement(tcPr, f'{{{A}}}solidFill')
            etree.SubElement(sf, f'{{{A}}}srgbClr').set('val', fill.lstrip('#'))
        else:
            etree.SubElement(tcPr, f'{{{A}}}noFill')
        cell.text_frame.clear()
        p = cell.text_frame.paragraphs[0]
        p.alignment = align
        r = p.add_run()
        r.text = text
        rPr = r._r.get_or_add_rPr()
        rPr.set('sz', str(int(size * 100)))
        rPr.set('b', '1' if bold else '0')
        etree.SubElement(rPr, f'{{{A}}}latin').set('typeface', FONT)
        sf = etree.SubElement(rPr, f'{{{A}}}solidFill')
        etree.SubElement(sf, f'{{{A}}}srgbClr').set('val', color.lstrip('#'))

    @staticmethod
    def _finish_table(shape):
        tbl = shape._element.find(f'.//{{{A}}}tbl')
        tblPr = tbl.find(f'{{{A}}}tblPr')
        if tblPr is None:
            tblPr = etree.Element(f'{{{A}}}tblPr')
            tbl.insert(0, tblPr)
        for ch in list(tblPr):
            tblPr.remove(ch)
        tblPr.set('firstRow', '1')
        tblPr.set('bandRow', '1')
        etree.SubElement(tblPr, f'{{{A}}}tableStyleId').text = \
            '{B301B821-A1FF-4177-AEE7-76D212191A09}'

    # ── Slide builders ───────────────────────────────────────────────────
    def title(self, title, subtitle):
        """Cover slide. Always the native Title layout, never a hand-built hero."""
        s = self.prs.slides.add_slide(self._layout('Title Slide Dark 2'))
        logo_x, wide = 0.6333, 11.0
        ph = s.placeholders[0]
        ph.left, ph.top = Inches(logo_x), Inches(1.6875)
        ph.width, ph.height = Inches(wide), Inches(2.8229)
        tf = ph.text_frame
        tf.clear()
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        r = p.add_run()
        r.text = title
        size = 48
        longest = max(title.split('\n'), key=len)
        while size > 28 and text_width_in(longest, size, True) > wide:
            size -= 2
        r.font.name, r.font.bold, r.font.size = FONT, True, Pt(size)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

        ph = s.placeholders[1]
        ph.left, ph.top = Inches(logo_x), Inches(4.5787)
        ph.width, ph.height = Inches(wide), Inches(0.75)
        tf = ph.text_frame
        tf.clear()
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        r = p.add_run()
        r.text = subtitle
        r.font.name, r.font.size = FONT, Pt(20)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        remove_empty_placeholders(s)
        return s

    def agenda(self, items, heading='AGENDA'):
        """Always the native Agenda layout — never a hand-built numbered list."""
        s = self.prs.slides.add_slide(self._layout('Agenda 2'))
        for ph in list(s.placeholders):
            idx = ph.placeholder_format.idx
            if idx == 0:
                ph.text_frame.text = heading
            elif idx == 13:
                tf = ph.text_frame
                tf.clear()
                for i, item in enumerate(items):
                    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                    p.text = item
        remove_empty_placeholders(s)
        return s

    def hero(self, line1, highlight, body=None, pills=None, icon=None,
             polarity='pos'):
        """Dark hero: optional icon plate, a white line, a gradient line, body, pills.
        Keep the case of `highlight` matching `line1` — both UPPERCASE here."""
        s = self._new(DARK_LAYOUT)
        y = 0.85
        if icon:
            self.icon_plate(s, 6.12, y, icon, dark=True, size=1.1)
            y = 2.35
        else:
            y = 1.9
        self.tb(s, 1.2, y, 10.9, 0.9, line1, size=40, bold=True, color=WHITE,
                align=PP_ALIGN.CENTER)
        hsize = 60
        while hsize > 30 and text_width_in(highlight, hsize, True) > 11.2:
            hsize -= 2
        self.grad_text(s, 1.0, y + 0.8, 11.3, 1.35, highlight, hsize, polarity=polarity)
        if body:
            self.tb(s, 1.7, y + 2.2, 9.9, 0.95, body, size=17, color=WHITE,
                    align=PP_ALIGN.CENTER, line_spacing=1.2)
        if pills:
            self.pill_row(s, 5.75, pills,
                          polarity='neg' if polarity == 'neg' else 'pos')
        return s

    def cards(self, title, subtitle, items, style='standard', note=None,
              body_size=11.5):
        """Light card grid, 2-4 cards in one row. `items` is
        [(icon, CARD TITLE, [body lines]), ...]. `note` adds a panel underneath."""
        s = self._new(LIGHT_LAYOUT)
        self.tb(s, CX, 0.38, CW, 0.62, title.upper(), size=TITLE_SIZE, bold=True,
                color=NAVY, line_spacing=1.0)
        if subtitle:
            self.tb(s, CX, 1.08, CW, 0.42, subtitle, size=SUBTITLE_SIZE, color=MUTED)
        n = len(items)
        gap = 0.27
        cwid = (CW - gap * (n - 1)) / n
        top = 1.72
        height = (4.35 if not note else 2.6)
        for i, (icon, ct, body) in enumerate(items):
            self.card(s, CX + i * (cwid + gap), top, cwid, height, icon, ct, body,
                      style=style, body_size=body_size)
        if note:
            head, text = note if isinstance(note, tuple) else (None, note)
            ny = top + height + 0.32
            nh = CBOTTOM - ny - 0.02
            self.rrect(s, CX, ny, CW, nh, fill=PANEL, line=BORDER)
            body = f"{head}\n{text}" if head else text
            self.tb(s, CX + 0.28, ny + 0.22, CW - 0.56, nh - 0.42, body, size=13,
                    color=INK, line_spacing=1.15, space_after=4)
        return s

    def flow(self, title, subtitle, stages, note=None, highlight_last=True):
        """Light architecture flow: 3-5 stages with arrows between them.
        `stages` is [(icon, STAGE TITLE, 'body\\ntext'), ...]."""
        s = self._new(LIGHT_LAYOUT)
        self.tb(s, CX, 0.38, CW, 0.62, title.upper(), size=TITLE_SIZE, bold=True,
                color=NAVY, line_spacing=1.0)
        if subtitle:
            self.tb(s, CX, 1.08, CW, 0.42, subtitle, size=SUBTITLE_SIZE, color=MUTED)
        n = len(stages)
        gap = 0.42
        bw = (CW - gap * (n - 1)) / n
        by = 1.72
        bh = 2.5 if note else 4.3
        for i, (icon, st, body) in enumerate(stages):
            bx = CX + i * (bw + gap)
            last = highlight_last and i == n - 1
            self.rrect(s, bx, by, bw, bh, fill=TINT if last else WHITE,
                       line='99DFFF' if last else BORDER)
            self.icon_plate(s, bx + 0.18, by + 0.2, icon, size=0.5)
            self.tb(s, bx + 0.18, by + 0.85, bw - 0.36, 0.42, st, size=12.5,
                    bold=True, color=NAVY, line_spacing=1.0)
            self.tb(s, bx + 0.18, by + 1.32, bw - 0.36, bh - 1.5, body, size=11,
                    color=GREY, line_spacing=1.15)
            if i < n - 1:
                self.arrow(s, bx + bw + 0.07, by + bh / 2 - 0.11, gap - 0.14, 0.22)
        if note:
            head, text = note if isinstance(note, tuple) else (None, note)
            ny = by + bh + 0.3
            nh = CBOTTOM - ny - 0.02
            self.rrect(s, CX, ny, CW, nh, fill=PANEL, line=BORDER)
            body = f"{head}\n{text}" if head else text
            self.tb(s, CX + 0.28, ny + 0.22, CW - 0.56, nh - 0.42, body, size=13,
                    color=INK, line_spacing=1.15, space_after=4)
        return s

    def table_slide(self, title, subtitle, headers, rows, col_widths=None,
                    row_heights=None, cards=None, side=None, body_size=11,
                    first_col_accent=True):
        """Light table slide. `cards` adds a row of cards underneath;
        `side` puts the table on the left and a panel on the right:
        side=(ICON, TITLE, 'body text')."""
        s = self._new(LIGHT_LAYOUT)
        self.tb(s, CX, 0.38, CW, 0.62, title.upper(), size=TITLE_SIZE, bold=True,
                color=NAVY, line_spacing=1.0)
        if subtitle:
            self.tb(s, CX, 1.08, CW, 0.42, subtitle, size=SUBTITLE_SIZE, color=MUTED)
        ty = 1.72
        tw = CW if not side else 5.85
        # A table may not grow past the content zone, and must leave room for the
        # card row underneath when there is one. Scale the requested row heights
        # down proportionally rather than letting the table run off the slide.
        budget = (CBOTTOM - ty - 0.1) if not cards else 2.6
        if row_heights:
            th = sum(row_heights)
            if th > budget:
                row_heights = [h * budget / th for h in row_heights]
                th = budget
        else:
            th = min(0.52 + len(rows) * 0.62, budget)
            row_heights = [0.52] + [(th - 0.52) / len(rows)] * len(rows)
        self.table(s, CX, ty, tw, th, headers, rows, col_widths=col_widths,
                   row_heights=row_heights, body_size=body_size,
                   first_col_accent=first_col_accent)
        if side:
            icon, st, body = side
            sx, sw = 6.75, CRIGHT - 6.75
            sh_h = CBOTTOM - ty - 0.02
            self.rrect(s, sx, ty, sw, sh_h, fill=WHITE, line=BORDER)
            self.icon_plate(s, sx + 0.25, ty + 0.23, icon, size=0.5)
            self.tb(s, sx + 0.87, ty + 0.27, sw - 1.1, 0.42, st, size=13, bold=True,
                    color=NAVY, line_spacing=1.0)
            self.tb(s, sx + 0.25, ty + 0.95, sw - 0.5, sh_h - 1.15, body, size=11.5,
                    color=GREY, line_spacing=1.12, space_after=6)
        if cards:
            n = len(cards)
            gap = 0.3
            cwid = (CW - gap * (n - 1)) / n
            cy = ty + th + 0.3
            ch_h = CBOTTOM - cy - 0.02
            for i, (icon, ct, body) in enumerate(cards):
                self.card(s, CX + i * (cwid + gap), cy, cwid, ch_h, icon, ct, body,
                          body_size=11.5)
        return s

    def split_dark(self, eyebrow, title_white, title_grad, steps, left_body=None,
                   polarity='pos'):
        """Dark split: title left, vertical divider, numbered points right.
        `steps` is [(NUMBER, HEADING, 'body text'), ...] — 2 or 3 of them."""
        s = self._new(DARK_LAYOUT)
        if eyebrow:
            self.eyebrow(s, 0.62, 0.55, 5.6, eyebrow)
        box = s.shapes.add_textbox(Inches(0.62), Inches(1.05), Inches(5.3), Inches(2.5))
        box.text_frame.word_wrap = True
        p = box.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        size = 38
        longest = max((title_white + title_grad).split('\n'), key=len)
        while size > 24 and text_width_in(longest, size, True) > 5.2:
            size -= 2
        if title_white:
            r = p.add_run()
            r.text = title_white
            r.font.name, r.font.bold, r.font.size = FONT, True, Pt(size)
            r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        if title_grad:
            r2 = p.add_run()
            r2.text = title_grad
            r2.font.name, r2.font.bold, r2.font.size = FONT, True, Pt(size)
            if polarity == 'pos':
                set_run_grad_fill(r2, GRAD_POS_DARK, angle_deg=0)
                add_run_glow(r2, blur_pt=15, color='4AFF9C', alpha=40)
            else:
                set_run_grad_fill(r2, GRAD_NEG_DARK, angle_deg=0)
                add_run_glow(r2, blur_pt=15, color='FFD836', alpha=25)
        if left_body:
            self.tb(s, 0.62, 3.8, 5.1, 2.4, left_body, size=13, color=WHITE,
                    line_spacing=1.2)
        div = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6.25), Inches(0.9),
                                 Inches(0.03), Inches(5.5))
        set_solid_fill(div, WHITE, alpha=20)
        set_no_line(div)
        n = len(steps)
        slot = 5.4 / n
        for i, (num, head, body) in enumerate(steps):
            y = 1.05 + i * slot
            self.tb(s, 6.75, y, 1.05, 0.85, num, size=40, bold=True, color=WHITE,
                    wrap=False)
            self.tb(s, 7.85, y + 0.06, 4.85, 0.46, head, size=16, bold=True,
                    color=WHITE, line_spacing=1.0)
            self.tb(s, 7.85, y + 0.58, 4.85, slot - 0.72, body, size=13, color=WHITE,
                    line_spacing=1.2)
        return s

    def steps_dark(self, eyebrow, title, steps):
        """Dark slide, 4 icon+text steps in a 2x2 grid."""
        s = self._new(DARK_LAYOUT)
        if eyebrow:
            self.eyebrow(s, 0.62, 0.45, 5.6, eyebrow)
        self.tb(s, 0.62, 0.9, 11.6, 0.62, title.upper(), size=TITLE_SIZE, bold=True,
                color=WHITE, line_spacing=1.0)
        rows = (len(steps) + 1) // 2
        row_h = (CBOTTOM - 1.85) / rows
        for i, (icon, head, body) in enumerate(steps):
            col, row = i % 2, i // 2
            x = 0.62 + col * 6.3
            y = 1.85 + row * row_h
            self.icon_plate(s, x, y, icon, dark=True, size=0.55)
            self.tb(s, x + 0.75, y + 0.04, 5.1, 0.42, head, size=16, bold=True,
                    color=WHITE, line_spacing=1.0)
            self.tb(s, x + 0.75, y + 0.52, 5.1, row_h - 0.72, body, size=12.5,
                    color=WHITE, line_spacing=1.2)
        return s

    def two_dark(self, eyebrow, title, panels):
        """Dark slide, two big numbered panels side by side.
        `panels` is [(NUMBER, HEADING, 'sub-heading', 'body text'), ...]."""
        s = self._new(DARK_LAYOUT)
        if eyebrow:
            self.eyebrow(s, 0.62, 0.45, 5.6, eyebrow)
        self.tb(s, 0.62, 0.9, 11.6, 0.62, title.upper(), size=TITLE_SIZE, bold=True,
                color=WHITE, line_spacing=1.0)
        gap = 0.3
        w = (CRIGHT - 0.62 - gap) / 2
        for i, (num, head, sub, body) in enumerate(panels):
            x = 0.62 + i * (w + gap)
            accent = '4AFF9C' if i == 0 else 'FE8A25'
            card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x),
                                      Inches(1.75), Inches(w), Inches(4.5))
            set_adj(card, 4667)
            set_solid_fill(card, WHITE, alpha=6)
            set_gradient_line(card, [('4AFF9C', 0), ('67FFFE', 100)] if i == 0
                              else [('FFD839', 0), ('ED2B3D', 100)])
            add_shadow(card, blur=15, color='4AFF9C' if i == 0 else 'FFD839', alpha=30)
            self.tb(s, x + 0.3, 1.98, 0.9, 0.7, num, size=34, bold=True, color=accent,
                    wrap=False)
            self.tb(s, x + 1.15, 2.05, w - 1.4, 0.46, head, size=15, bold=True,
                    color=WHITE, line_spacing=1.0)
            if sub:
                self.tb(s, x + 1.15, 2.5, w - 1.4, 0.36, sub, size=12, bold=True,
                        color=accent, line_spacing=1.0)
            self.tb(s, x + 0.3, 3.05, w - 0.6, 3.0, body, size=12, color=WHITE,
                    line_spacing=1.2, space_after=4)
        return s

    def ending(self, line, body=None):
        s = self.prs.slides.add_slide(self._layout('Ending Slide Dark 1'))
        ph = s.placeholders[10]
        ph.left, ph.top = Inches(0.6333), Inches(2.6)
        ph.width, ph.height = Inches(11.0), Inches(1.5)
        tf = ph.text_frame
        tf.clear()
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        r = p.add_run()
        r.text = line
        size = 44
        while size > 26 and text_width_in(max(line.split('\n'), key=len),
                                          size, True) > 11.0:
            size -= 2
        r.font.name, r.font.bold, r.font.size = FONT, True, Pt(size)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        remove_empty_placeholders(s)
        if body:
            self.tb(s, 0.6333, 4.2, 11.0, 1.2, body, size=17, color=WHITE,
                    line_spacing=1.3)
        return s

    # ── Save + finalize ──────────────────────────────────────────────────
    def save(self, quiet=False):
        icon_map: dict = {}
        for slide, rid, kw, color in self._icons:
            n = int(re.search(r'slide(\d+)\.xml', str(slide.part.partname)).group(1))
            icon_map.setdefault(n, []).append((rid, kw, color))
        self.prs.save(self.out)
        inject_svg_icons(self.out, icon_map)
        set_normal_view(self.out)
        scrub_document_metadata(self.out)
        empty = verify_icons_visible(self.out)
        if empty:
            raise AssertionError(f"transparent icon stubs: {empty}")
        problems = check_geometry(self.out)
        for p in problems:
            self.warn(p)
        n_icons = sum(len(v) for v in icon_map.values())
        if not quiet:
            status = 'OK' if not self._warnings else f"{len(self._warnings)} warning(s)"
            print(f"{Path(self.out).relative_to(REPO_ROOT)}  "
                  f"{len(self.prs.slides.__iter__.__self__._sldIdLst)} slides, "
                  f"{n_icons} icons  [{status}]")
            for w in self._warnings:
                print(f"    ! {w}")
        return self._warnings


def _tc_ln(side, color_hex=None, scheme=None, w=12700):
    ln = etree.Element(f'{{{A}}}{side}')
    for k, v in (('w', str(w)), ('cap', 'flat'), ('cmpd', 'sng'), ('algn', 'ctr')):
        ln.set(k, v)
    sf = etree.SubElement(ln, f'{{{A}}}solidFill')
    if scheme:
        etree.SubElement(sf, f'{{{A}}}schemeClr').set('val', scheme)
    else:
        etree.SubElement(sf, f'{{{A}}}srgbClr').set(
            'val', (color_hex or '000000').lstrip('#'))
    etree.SubElement(ln, f'{{{A}}}prstDash').set('val', 'solid')
    etree.SubElement(ln, f'{{{A}}}round')
    for tag in ('headEnd', 'tailEnd'):
        el = etree.SubElement(ln, f'{{{A}}}{tag}')
        el.set('type', 'none')
        el.set('w', 'med')
        el.set('len', 'med')
    return ln


# ── Post-save steps (all required — see the skill's Critical Rules) ────────
def inject_svg_icons(path, icon_map):
    """Embed the real vector SVGs over the PNG stubs (Critical Rule 16)."""
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    counter = [200]
    svg_added = False
    for num, icons in icon_map.items():
        skey = f'ppt/slides/slide{num}.xml'
        rkey = f'ppt/slides/_rels/slide{num}.xml.rels'
        if skey not in files or rkey not in files:
            continue
        sx, rx = files[skey].decode('utf-8'), files[rkey].decode('utf-8')
        rids = re.findall(r'Id="rId(\d+)"', rx)
        nxt = max(int(r) for r in rids) + 1 if rids else 1
        for rid, kw, color in icons:
            svg_bytes = colorize_svg(find_svg_icon(kw), color)
            media = f"icon_{re.sub(r'[^A-Za-z0-9_-]', '_', kw)}_{counter[0]}.svg"
            counter[0] += 1
            files[f'ppt/media/{media}'] = svg_bytes
            svg_added = True
            svg_rid = f'rId{nxt}'
            nxt += 1
            rx = rx.replace('</Relationships>',
                            f'<Relationship Id="{svg_rid}" Type="http://schemas.'
                            f'openxmlformats.org/officeDocument/2006/relationships/'
                            f'image" Target="../media/{media}"/>\n</Relationships>')
            pat = f'r:embed="rId{rid}"'
            if pat not in sx:
                continue
            ext = (f'<a:extLst><a:ext uri="{{96DAC541-7B7A-43D3-8B79-37D633B846F1}}">'
                   f'<asvg:svgBlip xmlns:asvg="{ASVG}" r:embed="{svg_rid}"/>'
                   f'</a:ext></a:extLst>')
            i = sx.index(pat)
            after = sx[i:]
            gt = after.index('>')
            if after[:gt + 1].endswith('/>'):
                old = after[:gt + 1]
                sx = sx.replace(old, old[:-2] + '>' + ext + '</a:blip>', 1)
            else:
                cp = sx.index('</a:blip>', i)
                sx = sx[:cp] + ext + sx[cp:]
        files[skey], files[rkey] = sx.encode('utf-8'), rx.encode('utf-8')
    # Without this content type PowerPoint reports "found a problem with content".
    if svg_added and '[Content_Types].xml' in files:
        ct = files['[Content_Types].xml'].decode('utf-8')
        if 'Extension="svg"' not in ct:
            ct = ct.replace('</Types>', '<Default Extension="svg" '
                                        'ContentType="image/svg+xml"/>\n</Types>')
            files['[Content_Types].xml'] = ct.encode('utf-8')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)


def set_normal_view(path):
    """The template ships lastView="sldMasterView" — without this the deck opens
    in Slide Master edit view (Critical Rule 17)."""
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    vp = 'ppt/viewProps.xml'
    if vp in files:
        t = files[vp].decode('utf-8')
        if 'lastView="sldMasterView"' in t:
            t = t.replace('lastView="sldMasterView"', 'lastView="sldView"')
        elif 'lastView=' not in t:
            t = t.replace('<p:viewPr ', '<p:viewPr lastView="sldView" ', 1)
        files[vp] = t.encode('utf-8')
    else:
        files[vp] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     b'<p:viewPr xmlns:p="http://schemas.openxmlformats.org/'
                     b'presentationml/2006/main" lastView="sldView"/>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)


def scrub_document_metadata(path, author='Veeam'):
    """Strip personal, agency and internal metadata from all seven parts that
    carry it (Critical Rule 20). Also the defence against a stale author name
    surviving into a published deck."""
    from xml.sax.saxutils import escape
    author = escape(author)
    with zipfile.ZipFile(path) as z:
        order = z.namelist()
        files = {n: z.read(n) for n in order}
    ct = '[Content_Types].xml'
    prels = 'ppt/_rels/presentation.xml.rels'
    rootrels = '_rels/.rels'
    drop = {'ppt/authors.xml'}
    if ct in files:
        files[ct] = re.sub(rb'<Override PartName="/ppt/authors\.xml"[^>]*/>', b'',
                           files[ct])
    if prels in files:
        files[prels] = re.sub(rb'<Relationship[^>]*Target="authors\.xml"[^>]*/>', b'',
                              files[prels])
    if 'docProps/custom.xml' in files:
        files['docProps/custom.xml'] = re.sub(
            rb'<property\b[^>]*\bname="Articulate(?:GUID|Path)"[^>]*>.*?</property>',
            b'', files['docProps/custom.xml'], flags=re.S)
    for n in list(files):
        if n.startswith(('customXml/', 'ppt/webextensions/', 'docProps/thumbnail.')):
            drop.add(n)
        if n.startswith('ppt/tags/') and n.endswith('.xml'):
            files[n] = re.sub(rb'<p:tag\b[^>]*\bname="(?:ARTICULATE|EE4P)[^"]*"[^>]*/>',
                              b'', files[n])
    if prels in files:
        files[prels] = re.sub(rb'<Relationship[^>]*Target="\.\./customXml/[^"]*"[^>]*/>',
                              b'', files[prels])
    if ct in files:
        for pat in (rb'<Override PartName="/customXml/itemProps\d+\.xml"[^>]*/>',
                    rb'<Override PartName="/ppt/webextensions/[^"]*"[^>]*/>',
                    rb'<Override PartName="/docProps/thumbnail\.[^"]*"[^>]*/>'):
            files[ct] = re.sub(pat, b'', files[ct])
    for r in (rootrels, prels):
        if r in files:
            files[r] = re.sub(
                rb'<Relationship[^>]*Target="[^"]*webextensions/taskpanes\.xml"[^>]*/>',
                b'', files[r])
    if rootrels in files:
        files[rootrels] = re.sub(
            rb'<Relationship[^>]*Target="docProps/thumbnail\.[^"]*"[^>]*/>', b'',
            files[rootrels])
    core = 'docProps/core.xml'
    if core in files:
        t = files[core].decode('utf-8')
        t = re.sub(r'<dc:creator>.*?</dc:creator>', f'<dc:creator>{author}</dc:creator>',
                   t, flags=re.S)
        t = re.sub(r'<cp:lastModifiedBy>.*?</cp:lastModifiedBy>',
                   f'<cp:lastModifiedBy>{author}</cp:lastModifiedBy>', t, flags=re.S)
        files[core] = t.encode('utf-8')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in order:
            if n not in drop:
                z.writestr(n, files[n])


def verify_icons_visible(path, min_frac=0.002):
    """Every icon PNG stub must be visible — some PowerPoint builds ignore the
    injected SVG and fall back to the raster blip (Critical Rule 16)."""
    bad = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith('ppt/media/') and n.lower().endswith('.png'):
                if not _png_visible(z.read(n), min_frac):
                    bad.append(n)
    return bad


def check_geometry(path, eps=0.02):
    """Post-build QA: no shape may sit outside the content zone."""
    prs = Presentation(path)
    problems = []

    def walk(shapes):
        for sh in shapes:
            yield sh
            if sh.shape_type == 6:
                yield from walk(sh.shapes)

    for i, slide in enumerate(prs.slides, 1):
        for sh in walk(slide.shapes):
            try:
                l, t = sh.left.inches, sh.top.inches
                w, h = sh.width.inches, sh.height.inches
            except Exception:
                continue
            if l < CX - eps or t < CY - eps or l + w > CRIGHT + eps \
                    or t + h > CBOTTOM + eps:
                label = (sh.text_frame.text[:40].replace('\n', '/')
                         if sh.has_text_frame and sh.text_frame.text.strip()
                         else str(sh.shape_type))
                problems.append(f"slide {i}: outside content zone "
                                f"[{l:.2f},{t:.2f} {w:.2f}x{h:.2f}] {label!r}")
    return problems
