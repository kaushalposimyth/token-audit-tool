#!/usr/bin/env node
/**
 * token_audit.js — Scan prompt files and report token usage + bloat patterns.
 *
 * Usage:
 *   node token_audit.js                      # audit all default dirs
 *   node token_audit.js --dir .claude/agents # specific directory
 *   node token_audit.js --file .claude/agents/editor.md
 *   node token_audit.js --top 10             # top 10 heaviest files
 *   node token_audit.js --json               # machine-readable JSON
 *   node token_audit.js --no-bloat           # summary only
 *
 * No npm install needed — uses only Node.js built-in modules.
 */

const fs   = require("fs");
const path = require("path");

// ---------------------------------------------------------------------------
// Token estimation (~4 chars per token for English prose)
// ---------------------------------------------------------------------------
function countTokens(text) {
  return Math.max(1, Math.floor(text.length / 4));
}

const TOKEN_METHOD = "approx (len/4)";

// ---------------------------------------------------------------------------
// Bloat detectors
// ---------------------------------------------------------------------------
function detectBloat(text) {
  const patterns = [];
  const lines = text.split("\n");

  // 1. Duplicate headers (same heading 3+ times)
  const headers = lines.filter(l => /^#{1,4}\s/.test(l.trim())).map(l => l.trim());
  const headerCounts = {};
  headers.forEach(h => { headerCounts[h] = (headerCounts[h] || 0) + 1; });
  const dupeHeaders = Object.entries(headerCounts).filter(([, c]) => c >= 3);
  if (dupeHeaders.length) {
    const total = dupeHeaders.reduce((s, [, c]) => s + c, 0);
    patterns.push({
      name: "duplicate_headers",
      description: "Same heading appears 3+ times",
      count: total,
      savings: countTokens(dupeHeaders.map(([h]) => h).join(" ")) * 2,
    });
  }

  // 2. Verbose preambles
  const preambleRe = /\b(in this (section|step|phase|part)|the purpose of this|this (section|step|guide|document|command) (will|is designed to|aims to|covers|explains)|below (you will find|is a|are the)|please (note that|keep in mind|be aware|ensure that you))\b/gi;
  const preambleMatches = text.match(preambleRe) || [];
  if (preambleMatches.length) {
    patterns.push({
      name: "verbose_preambles",
      description: "Filler intro phrases (can be cut or condensed)",
      count: preambleMatches.length,
      savings: preambleMatches.length * 8,
    });
  }

  // 3. Excessive blank lines (3+)
  const blankRuns = text.match(/\n{4,}/g) || [];
  if (blankRuns.length) {
    patterns.push({
      name: "excessive_blank_lines",
      description: "3+ consecutive blank lines (whitespace waste)",
      count: blankRuns.length,
      savings: blankRuns.reduce((s, r) => s + Math.floor(r.length / 4), 0),
    });
  }

  // 4. Very long bullet lists
  const bulletLines = lines.filter(l => /^[-*•]\s/.test(l.trim()));
  if (bulletLines.length > 20) {
    patterns.push({
      name: "very_long_bullet_list",
      description: `${bulletLines.length} bullet items — consider grouping or summarising`,
      count: bulletLines.length,
      savings: Math.max(0, (bulletLines.length - 15) * 6),
    });
  }

  // 5. Repeated multi-word phrases (5+ times)
  const phraseRe = /\b(\w+ \w+ \w+)\b/gi;
  const phraseCounts = {};
  let m;
  while ((m = phraseRe.exec(text.toLowerCase())) !== null) {
    const p = m[1];
    if (p.length > 12) phraseCounts[p] = (phraseCounts[p] || 0) + 1;
  }
  const noisyPhrases = Object.entries(phraseCounts).filter(([, c]) => c >= 5);
  if (noisyPhrases.length) {
    patterns.push({
      name: "repeated_phrases",
      description: "Multi-word phrases repeated 5+ times",
      count: noisyPhrases.length,
      savings: noisyPhrases.reduce((s, [ph, c]) => s + (c - 1) * countTokens(ph), 0),
    });
  }

  // 6. Over-specified output format (nested lettered sub-items)
  const formatSpecLines = lines.filter(l => /^\s{4,}[a-z]\.\s/.test(l));
  if (formatSpecLines.length > 10) {
    patterns.push({
      name: "detailed_output_spec",
      description: "Very nested output format spec — may be over-specified",
      count: formatSpecLines.length,
      savings: formatSpecLines.length * 5,
    });
  }

  // 7. Long code/example blocks (>40 lines)
  const codeBlockRe = /```[\s\S]*?```/g;
  const codeBlocks = text.match(codeBlockRe) || [];
  const longBlocks = codeBlocks.filter(b => (b.match(/\n/g) || []).length > 40);
  if (longBlocks.length) {
    patterns.push({
      name: "long_code_examples",
      description: `${longBlocks.length} code/example block(s) >40 lines`,
      count: longBlocks.length,
      savings: longBlocks.reduce((s, b) => s + Math.floor(countTokens(b) / 3), 0),
    });
  }

  return patterns;
}

// ---------------------------------------------------------------------------
// File collection
// ---------------------------------------------------------------------------
function collectFiles(roots, exts = new Set([".md", ".txt"])) {
  const files = [];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const stat = fs.statSync(root);
    if (stat.isFile()) {
      files.push(root);
    } else if (stat.isDirectory()) {
      walkDir(root, exts, files);
    }
  }
  return files;
}

function walkDir(dir, exts, out) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const e of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      walkDir(full, exts, out);
    } else if (exts.has(path.extname(e.name).toLowerCase())) {
      out.push(full);
    }
  }
}

// ---------------------------------------------------------------------------
// Audit a single file
// ---------------------------------------------------------------------------
function auditFile(filePath) {
  const text    = fs.readFileSync(filePath, "utf8");
  const tokens  = countTokens(text);
  const bloat   = detectBloat(text);
  const savings = bloat.reduce((s, b) => s + b.savings, 0);
  return {
    path:     filePath,
    relative: path.relative(process.cwd(), filePath),
    bytes:    fs.statSync(filePath).size,
    lines:    text.split("\n").length,
    tokens,
    savings,
    savingsPct: tokens > 0 ? Math.min(100, (savings / tokens) * 100) : 0,
    bloat,
  };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function bar(fraction, width = 20) {
  const filled = Math.round(fraction * width);
  return "#".repeat(filled) + ".".repeat(width - filled);
}

function severity(pct) {
  if (pct >= 20) return "high";
  if (pct >= 10) return "medium";
  return "low";
}

const COLORS = {
  high:   "\x1b[91m",
  medium: "\x1b[93m",
  low:    "\x1b[92m",
  reset:  "\x1b[0m",
};

function color(sev, text, useColor) {
  return useColor ? `${COLORS[sev]}${text}${COLORS.reset}` : text;
}

function renderText(reports, top, showBloat) {
  const useColor = process.stdout.isTTY;
  const sorted   = [...reports].sort((a, b) => b.tokens - a.tokens);
  const shown    = top ? sorted.slice(0, top) : sorted;
  const maxTok   = shown[0]?.tokens || 1;

  const totalTokens  = reports.reduce((s, r) => s + r.tokens, 0);
  const totalSavings = reports.reduce((s, r) => s + r.savings, 0);

  const SEP = "-".repeat(72);

  console.log(`\n${SEP}`);
  console.log(`  TOKEN AUDIT  --  ${reports.length} files  --  estimation: ${TOKEN_METHOD}`);
  console.log(`${SEP}\n`);

  for (const r of shown) {
    const sev    = severity(r.savingsPct);
    const frac   = r.tokens / maxTok;
    const tokLbl = color(sev, `${r.tokens.toLocaleString().padStart(6)} tok`, useColor);
    const savLbl = r.savings > 0
      ? color(sev, `  ~${r.savings.toLocaleString()} saveable (${Math.round(r.savingsPct)}%)`, useColor)
      : "";
    console.log(`  ${bar(frac)}  ${tokLbl}  ${r.relative}${savLbl}`);
    if (showBloat && r.bloat.length) {
      for (const b of r.bloat) {
        console.log(`    ${"·".padEnd(2)} ${b.name.padEnd(28)} ${b.description}  (x${b.count}, ~${b.savings} tok)`);
      }
    }
    console.log();
  }

  console.log(SEP);
  console.log(`  TOTAL   ${totalTokens.toLocaleString().padStart(8)} tokens across ${reports.length} files`);
  if (totalSavings > 0) {
    const pct = ((totalSavings / totalTokens) * 100).toFixed(1);
    console.log(`  SAVINGS ${totalSavings.toLocaleString().padStart(8)} tokens potentially removable  (${pct}%)`);
  }
  console.log(`${SEP}\n`);

  // Directory summary
  const dirs = {};
  for (const r of reports) {
    const d = path.dirname(r.relative);
    if (!dirs[d]) dirs[d] = { tokens: 0, files: 0, savings: 0 };
    dirs[d].tokens  += r.tokens;
    dirs[d].files   += 1;
    dirs[d].savings += r.savings;
  }

  console.log("  BY DIRECTORY\n");
  Object.entries(dirs)
    .sort(([, a], [, b]) => b.tokens - a.tokens)
    .forEach(([d, stats]) => {
      const pct = stats.tokens > 0 ? ((stats.savings / stats.tokens) * 100).toFixed(0) : 0;
      console.log(`  ${stats.tokens.toLocaleString().padStart(8)} tok  ${String(stats.files).padStart(3)} files  ${d}  (~${pct}% saveable)`);
    });

  console.log();
}

function renderJson(reports) {
  console.log(JSON.stringify({
    tokenMethod: TOKEN_METHOD,
    files: reports.map(r => ({
      path:          r.relative,
      tokens:        r.tokens,
      lines:         r.lines,
      bytes:         r.bytes,
      estimatedSavings: r.savings,
      savingsPct:    parseFloat(r.savingsPct.toFixed(1)),
      bloat:         r.bloat.map(b => ({
        pattern:     b.name,
        description: b.description,
        count:       b.count,
        savings:     b.savings,
      })),
    })),
  }, null, 2));
}

// ---------------------------------------------------------------------------
// CLI argument parsing (no external deps)
// ---------------------------------------------------------------------------
function parseArgs(argv) {
  const args = { dirs: [], files: [], top: null, json: false, noBloat: false };
  let i = 2;
  while (i < argv.length) {
    const a = argv[i];
    if (a === "--dir"  && argv[i + 1]) { args.dirs.push(argv[++i]); }
    else if (a === "--file" && argv[i + 1]) { args.files.push(argv[++i]); }
    else if (a === "--top"  && argv[i + 1]) { args.top = parseInt(argv[++i], 10); }
    else if (a === "--json")     { args.json = true; }
    else if (a === "--no-bloat") { args.noBloat = true; }
    i++;
  }
  return args;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
const DEFAULT_DIRS = [".claude/commands", ".claude/agents", "context"];

function main() {
  const args  = parseArgs(process.argv);
  const roots = [
    ...(args.dirs.length ? args.dirs : args.files.length ? [] : DEFAULT_DIRS),
    ...args.files,
  ];

  const files = collectFiles(roots);
  if (!files.length) {
    console.error("No .md/.txt files found. Run from your project root, or pass --dir / --file.");
    process.exit(1);
  }

  const reports = files.map(auditFile);

  if (args.json) {
    renderJson(reports);
  } else {
    renderText(reports, args.top, !args.noBloat);
  }
}

main();
