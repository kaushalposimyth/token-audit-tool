#!/usr/bin/env python3
"""
token_audit.py — Scan prompt files and report token usage + bloat patterns.

Usage:
    python token_audit.py                   # audit all prompt dirs
    python token_audit.py --dir .claude/commands
    python token_audit.py --file .claude/agents/editor.md
    python token_audit.py --top 10          # show only top 10 heaviest files
    python token_audit.py --json            # machine-readable output
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    TOKEN_METHOD = "tiktoken (cl100k_base)"
except ImportError:
    def count_tokens(text: str) -> int:
        # ~4 chars/token is a reasonable approximation for English prose
        return max(1, len(text) // 4)

    TOKEN_METHOD = "approx (len/4)"


# ---------------------------------------------------------------------------
# Bloat detectors
# ---------------------------------------------------------------------------

@dataclass
class BloatPattern:
    name: str
    description: str
    match_count: int = 0
    estimated_savings: int = 0  # tokens


def detect_bloat(text: str, total_tokens: int) -> list[BloatPattern]:
    patterns: list[BloatPattern] = []

    lines = text.splitlines()

    # 1. Repeated section headers (same header appears 3+ times)
    headers = [l.strip() for l in lines if re.match(r'^#{1,4}\s', l)]
    from collections import Counter
    header_counts = Counter(headers)
    dupes = {h: c for h, c in header_counts.items() if c >= 3}
    if dupes:
        p = BloatPattern("duplicate_headers", "Same heading appears 3+ times")
        p.match_count = sum(dupes.values())
        p.estimated_savings = count_tokens(" ".join(dupes.keys()) * 2)
        patterns.append(p)

    # 2. Verbose preambles ("In this section we will...", "The purpose of this...")
    preamble_re = re.compile(
        r'\b(in this (section|step|phase|part)|the purpose of this|'
        r'this (section|step|guide|document|command) (will|is designed to|aims to|covers|explains)|'
        r'below (you will find|is a|are the)|'
        r'please (note that|keep in mind|be aware|ensure that you))\b',
        re.IGNORECASE,
    )
    preamble_matches = preamble_re.findall(text)
    if preamble_matches:
        p = BloatPattern("verbose_preambles", "Filler intro phrases (can be cut or condensed)")
        p.match_count = len(preamble_matches)
        p.estimated_savings = p.match_count * 8
        patterns.append(p)

    # 3. Excessive blank lines (3+ consecutive)
    blank_runs = re.findall(r'\n{4,}', text)
    if blank_runs:
        p = BloatPattern("excessive_blank_lines", "3+ consecutive blank lines (whitespace waste)")
        p.match_count = len(blank_runs)
        p.estimated_savings = sum(len(r) // 4 for r in blank_runs)
        patterns.append(p)

    # 4. Long bullet lists that repeat a stem word
    bullet_lines = [l.strip() for l in lines if re.match(r'^[-*•]\s', l)]
    if len(bullet_lines) > 20:
        p = BloatPattern("very_long_bullet_list", f"{len(bullet_lines)} bullet items — consider grouping or summarising")
        p.match_count = len(bullet_lines)
        p.estimated_savings = max(0, (len(bullet_lines) - 15) * 6)
        patterns.append(p)

    # 5. Inline repetition: same multi-word phrase repeated many times
    phrase_re = re.compile(r'\b(\w+ \w+ \w+)\b')
    phrases = phrase_re.findall(text.lower())
    phrase_counts = Counter(phrases)
    noisy = {ph: c for ph, c in phrase_counts.items() if c >= 5 and len(ph) > 12}
    if noisy:
        p = BloatPattern("repeated_phrases", "Multi-word phrases repeated 5+ times")
        p.match_count = len(noisy)
        p.estimated_savings = sum((c - 1) * count_tokens(ph) for ph, c in noisy.items())
        patterns.append(p)

    # 6. Overly detailed output format specs (numbered + lettered sub-items)
    format_spec_lines = [l for l in lines if re.match(r'^\s{4,}[a-z]\.\s', l)]
    if len(format_spec_lines) > 10:
        p = BloatPattern("detailed_output_spec", "Very nested output format spec — may be over-specified")
        p.match_count = len(format_spec_lines)
        p.estimated_savings = len(format_spec_lines) * 5
        patterns.append(p)

    # 7. Example blocks that are very long (>40 lines between ``` fences)
    code_blocks = re.findall(r'```[\s\S]*?```', text)
    long_blocks = [b for b in code_blocks if b.count('\n') > 40]
    if long_blocks:
        p = BloatPattern("long_code_examples", f"{len(long_blocks)} code/example block(s) >40 lines")
        p.match_count = len(long_blocks)
        p.estimated_savings = sum(count_tokens(b) // 3 for b in long_blocks)
        patterns.append(p)

    return patterns


# ---------------------------------------------------------------------------
# File audit
# ---------------------------------------------------------------------------

@dataclass
class FileReport:
    path: Path
    size_bytes: int
    line_count: int
    token_count: int
    bloat: list[BloatPattern] = field(default_factory=list)
    estimated_savings: int = 0

    @property
    def relative_path(self) -> str:
        try:
            return str(self.path.relative_to(Path.cwd()))
        except ValueError:
            return str(self.path)

    @property
    def savings_pct(self) -> float:
        if self.token_count == 0:
            return 0.0
        return min(100.0, self.estimated_savings / self.token_count * 100)


def audit_file(path: Path) -> FileReport:
    text = path.read_text(encoding="utf-8", errors="replace")
    tokens = count_tokens(text)
    bloat = detect_bloat(text, tokens)
    savings = sum(b.estimated_savings for b in bloat)
    return FileReport(
        path=path,
        size_bytes=path.stat().st_size,
        line_count=text.count("\n"),
        token_count=tokens,
        bloat=bloat,
        estimated_savings=savings,
    )


def collect_files(roots: list[Path], exts: set[str] = {".md", ".txt"}) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file() and p.suffix.lower() in exts:
                    files.append(p)
    return files


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

SEVERITY_COLORS = {
    "high":   "\033[91m",  # red
    "medium": "\033[93m",  # yellow
    "low":    "\033[92m",  # green
    "reset":  "\033[0m",
}


def severity(savings_pct: float) -> str:
    if savings_pct >= 20:
        return "high"
    if savings_pct >= 10:
        return "medium"
    return "low"


def bar(fraction: float, width: int = 20) -> str:
    filled = round(fraction * width)
    return "#" * filled + "." * (width - filled)


def render_text(reports: list[FileReport], top: Optional[int], show_bloat: bool) -> None:
    # Force UTF-8 on Windows so box/block chars don't crash
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    use_color = sys.stdout.isatty()

    def color(sev: str, text: str) -> str:
        if not use_color:
            return text
        return f"{SEVERITY_COLORS[sev]}{text}{SEVERITY_COLORS['reset']}"

    total_tokens = sum(r.token_count for r in reports)
    total_savings = sum(r.estimated_savings for r in reports)

    SEP = "-" * 72
    print(f"\n{SEP}")
    print(f"  TOKEN AUDIT  --  {len(reports)} files  --  estimation: {TOKEN_METHOD}")
    print(f"{SEP}\n")

    sorted_reports = sorted(reports, key=lambda r: r.token_count, reverse=True)
    if top:
        sorted_reports = sorted_reports[:top]

    max_tokens = sorted_reports[0].token_count if sorted_reports else 1

    for r in sorted_reports:
        sev = severity(r.savings_pct)
        fraction = r.token_count / max_tokens
        label = color(sev, f"{r.token_count:>6,} tok")
        savings_label = ""
        if r.estimated_savings > 0:
            savings_label = color(sev, f"  ~{r.estimated_savings:,} saveable ({r.savings_pct:.0f}%)")
        print(f"  {bar(fraction)}  {label}  {r.relative_path}{savings_label}")
        if show_bloat and r.bloat:
            for b in r.bloat:
                print(f"    {'·':2} {b.name:<28} {b.description}  (×{b.match_count}, ~{b.estimated_savings} tok)")
        print()

    print(SEP)
    print(f"  TOTAL   {total_tokens:>8,} tokens across {len(reports)} files")
    if total_savings:
        print(f"  SAVINGS {total_savings:>8,} tokens potentially removable  ({total_savings/total_tokens*100:.1f}%)")
    print(f"{SEP}\n")

    # Group summary by directory
    dirs: dict[str, dict] = {}
    for r in reports:
        d = str(r.path.parent.relative_to(Path.cwd()) if r.path.is_relative_to(Path.cwd()) else r.path.parent)
        if d not in dirs:
            dirs[d] = {"tokens": 0, "files": 0, "savings": 0}
        dirs[d]["tokens"] += r.token_count
        dirs[d]["files"] += 1
        dirs[d]["savings"] += r.estimated_savings

    print("  BY DIRECTORY\n")
    for d, stats in sorted(dirs.items(), key=lambda x: x[1]["tokens"], reverse=True):
        pct = stats["savings"] / stats["tokens"] * 100 if stats["tokens"] else 0
        print(f"  {stats['tokens']:>8,} tok  {stats['files']:>3} files  {d}  (~{pct:.0f}% saveable)")
    print()


def render_json(reports: list[FileReport]) -> None:
    out = []
    for r in reports:
        out.append({
            "path": r.relative_path,
            "tokens": r.token_count,
            "lines": r.line_count,
            "bytes": r.size_bytes,
            "estimated_savings": r.estimated_savings,
            "savings_pct": round(r.savings_pct, 1),
            "bloat": [
                {
                    "pattern": b.name,
                    "description": b.description,
                    "count": b.match_count,
                    "savings": b.estimated_savings,
                }
                for b in r.bloat
            ],
        })
    print(json.dumps({"token_method": TOKEN_METHOD, "files": out}, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_DIRS = [
    ".claude/commands",
    ".claude/agents",
    "context",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit token usage in prompt/command/agent files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dir", action="append", metavar="PATH",
                        help="Directory to scan (repeatable). Default: .claude/commands .claude/agents context")
    parser.add_argument("--file", action="append", metavar="FILE",
                        help="Specific file to audit (repeatable)")
    parser.add_argument("--top", type=int, metavar="N",
                        help="Show only the N heaviest files")
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON")
    parser.add_argument("--no-bloat", action="store_true",
                        help="Skip per-file bloat pattern details")
    args = parser.parse_args()

    roots: list[Path] = []

    if args.dir:
        roots.extend(Path(d) for d in args.dir)
    elif not args.file:
        roots.extend(Path(d) for d in DEFAULT_DIRS if Path(d).exists())

    if args.file:
        roots.extend(Path(f) for f in args.file)

    if not roots:
        print("No directories or files found. Run from the repo root, or pass --dir / --file.")
        sys.exit(1)

    files = collect_files(roots)
    if not files:
        print(f"No .md/.txt files found under: {roots}")
        sys.exit(1)

    reports = [audit_file(f) for f in files]

    if args.json:
        render_json(reports)
    else:
        render_text(reports, top=args.top, show_bloat=not args.no_bloat)


if __name__ == "__main__":
    main()
