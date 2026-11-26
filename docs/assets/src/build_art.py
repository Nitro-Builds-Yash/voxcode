"""Generate the README artwork in docs/assets/.

    python docs/assets/src/build_art.py --fonts /path/to/fontsource-packages

GitHub strips CSS from READMEs, so the look (fonts and colours) lives in
these SVGs. Fonts are subset to the characters used and embedded as WOFF2
data URIs, so every viewer sees the same typography. Font packages come
from npm (`npm pack @fontsource/space-grotesk @fontsource/inter
@fontsource/jetbrains-mono`); all three are SIL Open Font License.
"""

from __future__ import annotations

import argparse
import base64
import io
import math
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1]

# ── Theme: "Ember" ──────────────────────────────────────────────────────────
BG_0, BG_1 = "#120d0b", "#211612"
CARD, BORDER = "#1a1310", "#3a2a22"
TEXT, MUTED, FAINT = "#fff7ed", "#d6c4b8", "#9a8579"
AMBER, CORAL, MAGENTA = "#fbbf24", "#fb7185", "#e879f9"
MINT, SKY, RED = "#6ee7b7", "#7dd3fc", "#f87171"
GRAD = [(0, AMBER), (0.5, CORAL), (1, MAGENTA)]

HEAD = "'Space Grotesk'"
BODY = "'Inter'"
MONO = "'JetBrains Mono'"

FONT_FILES = {
    ("Space Grotesk", 500): "space-grotesk/files/space-grotesk-latin-500-normal.woff2",
    ("Space Grotesk", 700): "space-grotesk/files/space-grotesk-latin-700-normal.woff2",
    ("Inter", 400): "inter/files/inter-latin-400-normal.woff2",
    ("Inter", 600): "inter/files/inter-latin-600-normal.woff2",
    ("JetBrains Mono", 400): "jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2",
    ("JetBrains Mono", 700): "jetbrains-mono/files/jetbrains-mono-latin-700-normal.woff2",
}


class Fonts:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, rel: str) -> Path:
        package, rest = rel.split("/", 1)
        matches = sorted(self.root.glob(f"fontsource-{package}-*/{rest}")) or sorted(
            self.root.glob(f"{package}/{rest}")
        )
        if not matches:
            raise SystemExit(f"font not found under {self.root}: {rel}")
        return matches[-1]

    def css(self, used: dict[tuple[str, int], set[str]]) -> str:
        from fontTools import subset
        from fontTools.ttLib import TTFont

        rules = []
        for (family, weight), chars in sorted(used.items()):
            if not chars:
                continue
            font = TTFont(self._path(FONT_FILES[(family, weight)]), recalcTimestamp=False)
            options = subset.Options()
            options.flavor = "woff2"
            options.layout_features = ["kern", "liga", "calt"]
            subsetter = subset.Subsetter(options)
            subsetter.populate(text="".join(sorted(chars | set(" "))))
            subsetter.subset(font)
            buffer = io.BytesIO()
            font["head"].modified = font["head"].created  # no build timestamp: reproducible output
            font.flavor = "woff2"
            font.save(buffer)
            data = base64.b64encode(buffer.getvalue()).decode()
            rules.append(
                f"@font-face{{font-family:'{family}';font-weight:{weight};"
                f"src:url(data:font/woff2;base64,{data}) format('woff2')}}"
            )
        return "".join(rules)


class Svg:
    """Tiny SVG builder that records which glyphs each font needs."""

    def __init__(self, width: int, height: int, label: str):
        self.w, self.h, self.label = width, height, label
        self.defs: list[str] = []
        self.body: list[str] = []
        self.used: dict[tuple[str, int], set[str]] = {}

    def add(self, markup: str) -> None:
        self.body.append(markup)

    def text(
        self, x, y, content, family=BODY, size=16, weight=400, fill=TEXT, anchor="start", extra="", spacing=0
    ):
        name = family.strip("'")
        weight = {
            "Space Grotesk": 700 if weight >= 600 else 500,
            "Inter": 600 if weight >= 600 else 400,
            "JetBrains Mono": 700 if weight >= 600 else 400,
        }[name]
        self.used.setdefault((name, weight), set()).update(content)
        self.add(
            f'<text x="{x}" y="{y}" font-family="{family}, sans-serif" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" letter-spacing="{spacing}" {extra}>{escape(content)}</text>'
        )

    def gradient(self, gid: str, stops=GRAD, x2=1, y2=0) -> str:
        s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
        self.defs.append(f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">{s}</linearGradient>')
        return f"url(#{gid})"

    def background(self, rx=22, grid=True, glow=(0.8, 0.3)):
        self.defs.append(
            f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG_0}"/>'
            f'<stop offset="1" stop-color="{BG_1}"/></linearGradient>'
            f'<radialGradient id="glow" cx="{glow[0]}" cy="{glow[1]}" r="0.6"><stop offset="0" stop-color="{CORAL}" '
            f'stop-opacity="0.22"/><stop offset="1" stop-color="{CORAL}" stop-opacity="0"/></radialGradient>'
            '<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">'
            '<circle cx="2" cy="2" r="1.1" fill="#ffffff" fill-opacity="0.06"/></pattern>'
        )
        self.add(f'<rect width="{self.w}" height="{self.h}" rx="{rx}" fill="url(#bg)"/>')
        if grid:
            self.add(f'<rect width="{self.w}" height="{self.h}" rx="{rx}" fill="url(#dots)"/>')
        self.add(f'<rect width="{self.w}" height="{self.h}" rx="{rx}" fill="url(#glow)"/>')
        self.add(
            f'<rect x="0.5" y="0.5" width="{self.w - 1}" height="{self.h - 1}" rx="{rx}" fill="none" '
            f'stroke="#ffffff" stroke-opacity="0.07"/>'
        )

    def render(self, fonts: Fonts) -> str:
        style = f"<style>{fonts.css(self.used)}</style>"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.label)}">'
            f"<defs>{style}{''.join(self.defs)}</defs>{''.join(self.body)}</svg>\n"
        )


def fade_in(begin: float) -> str:
    return (
        f'<animate attributeName="opacity" from="0" to="1" begin="{begin:.2f}s" dur="0.35s" fill="freeze"/>'
    )


def window(svg: Svg, x, y, w, h, title: str):
    svg.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{CARD}" stroke="{BORDER}"/>')
    svg.add(
        f'<path d="M{x} {y + 40}V{y + 16}a16 16 0 0 1 16-16H{x + w - 16}a16 16 0 0 1 16 16V{y + 40}Z" fill="#241a15"/>'
    )
    for i, colour in enumerate((RED, AMBER, MINT)):
        svg.add(f'<circle cx="{x + 24 + i * 20}" cy="{y + 20}" r="6" fill="{colour}" fill-opacity="0.85"/>')
    svg.text(x + w / 2, y + 25, title, MONO, 13, fill=FAINT, anchor="middle")


# ── Artwork ─────────────────────────────────────────────────────────────────
def hero() -> Svg:
    svg = Svg(1280, 460, "VoxCode — talk to your codebase, get answers and tested patches back")
    svg.background(glow=(0.78, 0.4))
    grad = svg.gradient("grad")
    svg.add(
        f'<rect x="112" y="78" width="214" height="32" rx="16" fill="{AMBER}" fill-opacity="0.1" stroke="{AMBER}" stroke-opacity="0.4"/>'
    )
    svg.add(
        f'<circle cx="132" cy="94" r="5" fill="{CORAL}"><animate attributeName="opacity" values="1;0.25;1" dur="1.6s" repeatCount="indefinite"/></circle>'
    )
    svg.text(146, 99, "VOICE · RUST · LOCAL-FIRST", MONO, 12, 700, AMBER, spacing=1)
    svg.add(
        f'<text x="108" y="196" font-family="{HEAD}, sans-serif" font-size="88" font-weight="700" letter-spacing="-3" '
        f'fill="{TEXT}">vox<tspan fill="{grad}">code</tspan></text>'
    )
    svg.used.setdefault(("Space Grotesk", 700), set()).update("voxcode")
    svg.text(112, 246, "Talk to your codebase.", HEAD, 30, 500, TEXT)
    svg.text(112, 284, "Get answers and tested patches back.", HEAD, 30, 500, MUTED)
    for i in range(32):
        x = 112 + i * 14
        base = 8 + 26 * abs(math.sin(i * 0.55)) * (0.4 + 0.6 * abs(math.cos(i * 0.23)))
        high = base + 26 * abs(math.sin(i * 1.3 + 1))
        dur = 0.85 + 0.06 * (i % 7)
        y0, y1 = 362 - base / 2, 362 - high / 2
        svg.add(
            f'<rect x="{x}" y="{y0:.1f}" width="8" height="{base:.1f}" rx="4" fill="{grad}">'
            f'<animate attributeName="height" values="{base:.1f};{high:.1f};{base:.1f}" dur="{dur:.2f}s" repeatCount="indefinite"/>'
            f'<animate attributeName="y" values="{y0:.1f};{y1:.1f};{y0:.1f}" dur="{dur:.2f}s" repeatCount="indefinite"/></rect>'
        )
    svg.text(112, 420, "Python · TypeScript · Rust  ·  any LLM  ·  VS Code · MCP · CLI", BODY, 15, 400, FAINT)

    window(svg, 704, 70, 470, 320, "voxcode — auth.py")
    svg.add(
        f'<rect x="724" y="128" width="430" height="46" rx="23" fill="{CORAL}" fill-opacity="0.12" stroke="{CORAL}" stroke-opacity="0.45"/>'
    )
    svg.add(f'<circle cx="748" cy="151" r="9" fill="{CORAL}" fill-opacity="0.9"/>')
    svg.add(f'<rect x="745" y="145" width="6" height="9" rx="3" fill="{BG_0}"/>')
    svg.text(768, 157, "“Fix the password check in login”", BODY, 16, 400, TEXT)
    steps = [
        (MINT, "✓ found login() · 3 callers"),
        (MINT, "✓ patch applies · tests pass"),
    ]
    for i, (colour, line) in enumerate(steps):
        svg.add(f'<g opacity="0">{fade_in(0.5 + i * 0.4)}')
        svg.text(734, 206 + i * 22, line, MONO, 13, 400, colour)
        svg.add("</g>")
    svg.add(f'<line x1="734" y1="246" x2="1144" y2="246" stroke="{BORDER}"/>')
    diff = [
        (FAINT, "@@ src/auth.py @@"),
        (RED, "-    if user.password == password:"),
        (MINT, "+    if verify_hash(password, user.hash):"),
        (MUTED, "         return issue_token(user)"),
    ]
    for i, (colour, line) in enumerate(diff):
        svg.add(f'<g opacity="0">{fade_in(1.3 + i * 0.3)}')
        if line[0] in "+-":
            svg.add(
                f'<rect x="726" y="{257 + i * 24}" width="426" height="22" rx="4" fill="{colour}" fill-opacity="0.1"/>'
            )
        svg.text(734, 273 + i * 24, line, MONO, 14, 400, colour)
        svg.add("</g>")
    svg.add(f'<g opacity="0">{fade_in(2.6)}')
    svg.add(f'<rect x="968" y="350" width="86" height="28" rx="8" fill="{grad}"/>')
    svg.text(1011, 369, "Accept", BODY, 13, 600, BG_0, "middle")
    svg.add(f'<rect x="1062" y="350" width="86" height="28" rx="8" fill="none" stroke="{BORDER}"/>')
    svg.text(1105, 369, "Reject", BODY, 13, 600, MUTED, "middle")
    svg.add("</g>")
    return svg


def section(name: str, number: int, title: str, subtitle: str) -> tuple[str, Svg]:
    # Its own dark bar, so it reads the same on GitHub's light and dark themes.
    svg = Svg(1280, 88, title)
    svg.background(rx=16, grid=False, glow=(0.0, 0.5))
    grad = svg.gradient("grad")
    svg.add(f'<rect x="0" y="16" width="4" height="56" rx="2" fill="{grad}"/>')
    svg.text(30, 54, f"{number:02d}", MONO, 15, 700, AMBER, spacing=1)
    svg.add(f'<rect x="64" y="45" width="24" height="3" rx="1.5" fill="{grad}"/>')
    svg.text(104, 57, title, HEAD, 34, 700, TEXT, spacing=-0.8)
    svg.text(1250, 55, subtitle, BODY, 15, 400, FAINT, "end")
    return f"section-{name}.svg", svg


SECTIONS = [
    ("features", "Features", "what it does"),
    ("quick-start", "Quick start", "running in a minute"),
    ("integrations", "Use it anywhere", "editor · MCP · terminal · HTTP"),
    ("how-it-works", "How it works", "from voice to verified patch"),
    ("review", "Review before it runs", "annotate plans and patches"),
    ("learning", "It learns from you", "ranking · routing · grounding"),
    ("rust-core", "Rust core", "same results, much faster"),
    ("benchmarks", "Measured, not guessed", "benchmarks you can rerun"),
    ("layout", "Project layout", "where things live"),
    ("development", "Development", "tests, lint, build"),
]


def features() -> Svg:
    cards = [
        (
            "🎙️",
            "Voice-first",
            AMBER,
            [
                "Push-to-talk or hands-free with",
                "a wake word. Whisper runs locally;",
                "replies stream out loud and you",
                "can interrupt mid-sentence.",
            ],
        ),
        (
            "🔎",
            "Search that learns",
            CORAL,
            [
                "BM25 + vectors, a call graph and git",
                "history, re-ranked by a model that",
                "trains on your docstrings and every",
                "accepted patch or thumbs-up.",
            ],
        ),
        (
            "✅",
            "Patches you can trust",
            MINT,
            [
                "Plan → edit → test → repair.",
                "git apply --check, tests in a",
                "network-isolated sandbox, then",
                "you accept. Undo is one word.",
            ],
        ),
        (
            "🦀",
            "Rust-accelerated",
            "#fb923c",
            [
                "Search, scanning, redaction and",
                "voice detection in Rust: up to",
                "157× faster, identical results,",
                "pure-Python fallback.",
            ],
        ),
        (
            "🔌",
            "Any model, used wisely",
            MAGENTA,
            [
                "Easy questions go to a small model,",
                "hard ones to the big one. Answers",
                "are checked for invented names;",
                "repeated questions cost nothing.",
            ],
        ),
        (
            "🔒",
            "Private by default",
            SKY,
            [
                "Localhost only. Secrets redacted",
                "before anything leaves. LOCAL_ONLY",
                "mode. Indexes live in ~/.voxcode,",
                "never in your repository.",
            ],
        ),
    ]
    w, h, gap = 408, 232, 24
    svg = Svg(3 * w + 2 * gap, 2 * h + gap, "VoxCode features")
    for i, (icon, title, colour, lines) in enumerate(cards):
        x, y = (i % 3) * (w + gap), (i // 3) * (h + gap)
        svg.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{CARD}" stroke="{BORDER}"/>')
        svg.add(f'<rect x="{x + 24}" y="{y}" width="64" height="3" rx="1.5" fill="{colour}"/>')
        svg.add(f'<circle cx="{x + 50}" cy="{y + 54}" r="26" fill="{colour}" fill-opacity="0.13"/>')
        svg.add(f'<text x="{x + 50}" y="{y + 63}" font-size="24" text-anchor="middle">{icon}</text>')
        svg.text(x + 90, y + 62, title, HEAD, 22, 700, TEXT, spacing=-0.3)
        for j, line in enumerate(lines):
            svg.text(x + 26, y + 120 + j * 25, line, BODY, 15.5, 400, MUTED)
    return svg


def demo() -> Svg:
    lines = [
        (FAINT, "$ ", TEXT, "voxcode index ."),
        (MINT, "✓ ", MUTED, "indexed 175 files · symbols, vectors and call graph ready"),
        (FAINT, "$ ", TEXT, 'voxcode ask "how are patches validated?"'),
        (CORAL, "› ", MUTED, "Every diff goes through git apply --check, then your tests"),
        (CORAL, "  ", MUTED, "run in a sandbox copy (Docker when available). Failures are"),
        (CORAL, "  ", MUTED, "fed back for a repair pass. → execution/patch/patch_service.py"),
        (FAINT, "$ ", TEXT, 'voxcode edit "add a docstring to reciprocal_rank_fusion" --tests'),
        (MINT, "✓ ", MUTED, "plan → edit → tests passed · 1 file changed"),
    ]
    height = 96 + len(lines) * 30
    svg = Svg(960, height, "VoxCode command-line demo")
    svg.background(rx=16, grid=False, glow=(0.9, 0.0))
    window(svg, 0, 0, 960, height, "~/projects/voxcode")
    for i, (pc, prompt, tc, text) in enumerate(lines):
        y = 82 + i * 30
        svg.add(f'<g opacity="0">{fade_in(0.4 + i * 0.7)}')
        svg.text(36, y, prompt, MONO, 15.5, 700, pc)
        svg.text(58, y, text, MONO, 15.5, 400, tc)
        svg.add("</g>")
    y = 82 + len(lines) * 30
    svg.add(
        f'<rect x="36" y="{y - 15}" width="10" height="20" rx="2" fill="{AMBER}"><animate attributeName="opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/></rect>'
    )
    return svg


def pipeline() -> Svg:
    steps = [
        ("Speak", "Whisper + VAD, or type", AMBER),
        ("Understand", "question or change?", "#fb923c"),
        ("Retrieve", "hybrid search + graph", CORAL),
        ("Generate", "any LLM, streamed", "#f472b6"),
        ("Verify", "tests in a sandbox", MAGENTA),
        ("You decide", "accept · branch · PR", MINT),
    ]
    w, gap = 186, 26
    svg = Svg(
        6 * w + 5 * gap + 64,
        210,
        "VoxCode pipeline: speak, understand, retrieve, generate, verify, you decide",
    )
    svg.background(glow=(0.5, 0.0))
    svg.text(32, 42, "HOW A REQUEST FLOWS", MONO, 12, 700, FAINT, spacing=2)
    for i, (title, sub, colour) in enumerate(steps):
        x = 32 + i * (w + gap)
        svg.add(
            f'<rect x="{x}" y="62" width="{w}" height="118" rx="14" fill="{CARD}" stroke="{colour}" stroke-opacity="0.45"/>'
        )
        svg.add(f'<rect x="{x + 16}" y="61" width="44" height="3" rx="1.5" fill="{colour}"/>')
        svg.text(x + 18, 98, f"{i + 1:02d}", MONO, 13, 700, colour)
        svg.text(x + 18, 132, title, HEAD, 22, 700, TEXT, spacing=-0.3)
        svg.text(x + 18, 158, sub, BODY, 13.5, 400, MUTED)
        if i < len(steps) - 1:
            ax = x + w + 4
            svg.add(
                f'<path d="M{ax} 121h{gap - 10}m-6-6 6 6-6 6" fill="none" stroke="{FAINT}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            )
    return svg


def rust_bench() -> Svg:
    # Measured in CI (2-core GitHub runner); speed-ups from unrounded timings.
    data = [
        ("BM25 search (4 queries)", 43.2, 0.3, "157×"),
        ("Voice activity detection (60 s)", 16.4, 1.3, "12×"),
        ("Build search index (20k chunks)", 2029.9, 401.0, "5.1×"),
        ("Scan repository (175 files)", 31.5, 6.3, "5.0×"),
        ("Redact secrets (0.5 MB)", 205.7, 52.5, "3.9×"),
    ]
    width, top, row = 960, 86, 66
    svg = Svg(width, top + row * len(data) + 44, "Rust core speed-ups over pure Python")
    svg.background(glow=(0.9, 0.1))
    rust = svg.gradient("rust", [(0, AMBER), (1, "#f97316")])
    svg.text(32, 50, "Rust core vs pure Python", HEAD, 24, 700, TEXT, spacing=-0.4)
    svg.text(width - 32, 50, "lower bar = faster", BODY, 13, 400, FAINT, "end")
    bx, bw = 350, width - 350 - 170
    for i, (name, py, rs, speed) in enumerate(data):
        y = top + i * row
        svg.text(32, y + 30, name, BODY, 15.5, 400, MUTED)
        svg.add(
            f'<rect x="{bx}" y="{y + 10}" width="{bw}" height="12" rx="6" fill="{FAINT}" fill-opacity="0.35"/>'
        )
        svg.text(bx + bw + 10, y + 21, f"{py:,.1f} ms", MONO, 12, 400, FAINT)
        w = max(5, bw * rs / py)
        svg.add(
            f'<rect x="{bx}" y="{y + 30}" width="{w:.1f}" height="12" rx="6" fill="{rust}"><animate attributeName="width" from="0" to="{w:.1f}" dur="1.1s" fill="freeze"/></rect>'
        )
        svg.text(round(bx + w + 10, 1), y + 41, f"{rs:,.1f} ms", MONO, 12, 400, AMBER)
        svg.text(width - 32, y + 38, speed, HEAD, 22, 700, "#fb923c", "end")
    y = svg.h - 22
    svg.add(f'<rect x="32" y="{y - 10}" width="14" height="10" rx="3" fill="{FAINT}" fill-opacity="0.35"/>')
    svg.text(52, y, "Python", BODY, 12.5, 400, FAINT)
    svg.add(f'<rect x="112" y="{y - 10}" width="14" height="10" rx="3" fill="{rust}"/>')
    svg.text(
        132, y, "Rust (voxcode-native) · measured in CI on a 2-core GitHub runner", BODY, 12.5, 400, FAINT
    )
    return svg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fonts", type=Path, required=True, help="folder with unpacked @fontsource packages")
    fonts = Fonts(parser.parse_args().fonts)
    art = {
        "hero.svg": hero(),
        "features.svg": features(),
        "demo.svg": demo(),
        "pipeline.svg": pipeline(),
        "rust-bench.svg": rust_bench(),
    }
    for number, (name, title, subtitle) in enumerate(SECTIONS, 1):
        filename, svg = section(name, number, title, subtitle)
        art[filename] = svg
    for filename, svg in art.items():
        (OUT / filename).write_text(svg.render(fonts), encoding="utf-8")
        print(f"{filename:28} {(OUT / filename).stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
