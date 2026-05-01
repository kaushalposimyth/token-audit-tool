# Token Audit Tool

A Python script that scans your AI prompt files (commands, agents, context docs) and reports which files are too heavy, where the waste is, and how many tokens you can save.

---

## What It Does

Every time you run an AI command, your prompt files get sent to the model. Bigger files = more tokens = higher cost + slower responses.

This tool scans your `.claude/commands/`, `.claude/agents/`, and `context/` folders and tells you:

- Token count per file
- Which files are the heaviest
- What kind of bloat is causing it (long bullet lists, repeated phrases, filler text, etc.)
- Estimated tokens you can remove

---

## Requirements

**Python version (`token_audit.py`)**
- Python 3.8+
- No extra packages needed (uses built-in libraries)
- Optional: install `tiktoken` for accurate token counts

```bash
pip install tiktoken
```

**JavaScript version (`token_audit.js`)**
- Node.js 14+
- No npm install needed — uses only built-in Node.js modules

---

## Setup

1. Copy `token_audit.py` **or** `token_audit.js` into your project root (the folder that contains `.claude/` and `context/`)
2. Run it

---

## Usage

### Python
```bash
# Full audit — scans .claude/commands/, .claude/agents/, context/
py token_audit.py

# Show only the 10 heaviest files
py token_audit.py --top 10

# Audit a specific directory
py token_audit.py --dir .claude/agents

# Audit a specific file
py token_audit.py --file .claude/agents/editor.md

# Summary only (no per-file bloat details)
py token_audit.py --no-bloat

# Machine-readable JSON output
py token_audit.py --json
```

### JavaScript
```bash
# Full audit
node token_audit.js

# Show only the 10 heaviest files
node token_audit.js --top 10

# Audit a specific directory
node token_audit.js --dir .claude/agents

# Audit a specific file
node token_audit.js --file .claude/agents/editor.md

# Summary only
node token_audit.js --no-bloat

# Machine-readable JSON output
node token_audit.js --json
```

---

## Sample Output

```
------------------------------------------------------------------------
  TOKEN AUDIT  --  46 files  --  estimation: approx (len/4)
------------------------------------------------------------------------

  ####################   5,526 tok  context/seo-guidelines.md  ~1,737 saveable (31%)
    ·  very_long_bullet_list   300 bullet items — consider grouping  (×300, ~1710 tok)
    ·  duplicate_headers       Same heading appears 3+ times         (×3,   ~15 tok)

  ################....   4,397 tok  .claude/commands/article.md  ~1,086 saveable (25%)
  ###############.....   4,170 tok  .claude/commands/write.md    ~900 saveable (22%)

------------------------------------------------------------------------
  TOTAL      97,219 tokens across 46 files
  SAVINGS    23,063 tokens potentially removable  (23.7%)
------------------------------------------------------------------------

  BY DIRECTORY

    44,258 tok   24 files  .claude/commands  (~22% saveable)
    27,863 tok   11 files  .claude/agents    (~24% saveable)
    25,098 tok   11 files  context           (~27% saveable)
```

---

## Bloat Patterns Detected

| Pattern | What It Means |
|---|---|
| `very_long_bullet_list` | 20+ bullet items — group or trim to the most important ones |
| `duplicate_headers` | Same heading used 3+ times — consider merging sections |
| `verbose_preambles` | Filler phrases like "In this section we will..." — safe to delete |
| `repeated_phrases` | Same multi-word phrase used 5+ times — consolidate |
| `long_code_examples` | Code block over 40 lines — trim or summarise |
| `detailed_output_spec` | Very nested output format spec — may be over-specified |
| `excessive_blank_lines` | 3+ blank lines in a row — wasted whitespace |

---

## Default Directories Scanned

```
.claude/commands/
.claude/agents/
context/
```

You can override these with `--dir` flags.

---

## License

MIT
