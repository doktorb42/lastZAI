"""Generate a local-only, read-only visual review page for the Agency datasets."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parent
SOURCES = (
    ("v0", ROOT / "data" / "training" / "agency" / "synthetic" / "peva_agency_synthetic_v0.jsonl"),
    ("v1", ROOT / "data" / "training" / "agency" / "synthetic" / "peva_agency_synthetic_v1.jsonl"),
)
OUTPUT = ROOT / "data" / "training" / "agency" / "review" / "peva_agency_dataset_review.html"
EXPECTED_COUNTS = {"v0": 80, "v1": 80, "total": 160}
LONG_MESSAGE_CHARS = 180
SILENT_MESSAGE_WARNING_CHARS = 80
ASSISTANT_PHRASES = ("how can i help", "how can i assist", "let me know if", "sure thing")
SILENT_DECISIONS = {"IGNORE", "WAIT", "DO_NOTHING"}


def _read_jsonl(source_name: str, path: Path, start_index: int) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing Agency dataset source: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            source = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path.name}:{line_number}: {exc.msg}") from exc
        if not isinstance(source, dict):
            raise ValueError(f"Expected a JSON object in {path.name}:{line_number}")
        row = dict(source)
        row["_review"] = {"number": start_index + len(rows), "source_file": source_name}
        rows.append(row)
    return rows


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized(value: Any) -> str:
    return _text(value).casefold()


def _target(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("target")
    return value if isinstance(value, Mapping) else {}


def _is_action_safety(row: Mapping[str, Any]) -> bool:
    marker = f"{row.get('category_group', '')} {row.get('category', '')}".casefold()
    return "action" in marker or "safety" in marker


def _is_personality(row: Mapping[str, Any]) -> bool:
    marker = f"{row.get('category_group', '')} {row.get('category', '')}".casefold()
    return "personality" in marker or "identity" in marker


def _has_memory_or_context(row: Mapping[str, Any]) -> bool:
    return bool(row.get("player_memories") or row.get("context"))


def _duplicate_values(rows: Iterable[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    inputs = Counter(_normalized(row.get("input")) for row in rows if _normalized(row.get("input")))
    messages = Counter(_normalized(_target(row).get("message")) for row in rows if _normalized(_target(row).get("message")))
    return ({value for value, count in inputs.items() if count > 1}, {value for value, count in messages.items() if count > 1})


def _add_review_metadata(rows: list[dict[str, Any]]) -> None:
    duplicate_inputs, duplicate_messages = _duplicate_values(rows)
    for row in rows:
        target = _target(row)
        decision = _text(target.get("decision")).upper()
        message = _text(target.get("message"))
        folded = message.casefold()
        warnings: list[str] = []
        if len(message) >= LONG_MESSAGE_CHARS:
            warnings.append(f"LONG MESSAGE ({len(message)} chars)")
        if _normalized(row.get("input")) in duplicate_inputs or _normalized(message) in duplicate_messages:
            warnings.append("DUPLICATE-LOOKING TEXT")
        if any(phrase in folded for phrase in ASSISTANT_PHRASES):
            warnings.append("ASSISTANT-LIKE PHRASE")
        if decision == "REPLY" and not message:
            warnings.append("REPLY HAS NO MESSAGE")
        if decision in SILENT_DECISIONS and len(message) >= SILENT_MESSAGE_WARNING_CHARS:
            warnings.append("SILENT DECISION HAS LONG MESSAGE")
        if row.get("mode") == "AGENCY_PROACTIVE" and decision == "START_CONVERSATION" and not _text(target.get("target_uid")):
            warnings.append("START_CONVERSATION HAS NO TARGET UID")
        review = dict(row["_review"])
        review.update({
            "warnings": warnings,
            "action_safety": _is_action_safety(row),
            "memory_context": _has_memory_or_context(row),
            "personality": _is_personality(row),
            "control_silence": decision in SILENT_DECISIONS,
        })
        row["_review"] = review


def _summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    rows = list(rows)
    decisions = Counter(_text(_target(row).get("decision")).upper() for row in rows)
    modes = Counter(_text(row.get("mode")).upper() for row in rows)
    return {
        "TOTAL": len(rows),
        "REACTIVE": modes["AGENCY_REACTIVE"],
        "PROACTIVE": modes["AGENCY_PROACTIVE"],
        "REPLY": decisions["REPLY"],
        "IGNORE": decisions["IGNORE"],
        "WAIT": decisions["WAIT"],
        "ASK_BACK": decisions["ASK_BACK"],
        "CHANGE_TOPIC": decisions["CHANGE_TOPIC"],
        "PROPOSE_GAME_ACTION": decisions["PROPOSE_GAME_ACTION"],
        "START_CONVERSATION": decisions["START_CONVERSATION"],
        "DO_NOTHING": decisions["DO_NOTHING"],
        "ACTION/SAFETY": sum(_is_action_safety(row) for row in rows),
        "MEMORY": sum(bool(row.get("player_memories")) for row in rows),
        "PERSONALITY": sum(_is_personality(row) for row in rows),
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PEva Agency Dataset Review</title>
<style>
  :root { color-scheme: dark; --bg:#0d1117; --panel:#161b22; --panel2:#212936; --line:#30363d; --text:#e6edf3; --muted:#9da7b3; --accent:#7ee787; --blue:#79c0ff; --warn:#f2cc60; --bad:#ff7b72; --violet:#d2a8ff; }
  * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }
  button,select,input,textarea { font:inherit; } .shell { max-width:1540px; margin:auto; padding:26px; }
  h1 { margin:0 0 4px; font-size:28px; } h2 { font-size:17px; margin:0; } .lede,.muted { color:var(--muted); } .lede { margin:0 0 22px; }
  .validation { padding:13px 16px; border:1px solid var(--line); background:var(--panel); border-radius:10px; margin:0 0 18px; font-weight:650; }
  .validation.ok { border-color:#2ea043; color:#aff5b4; } .validation.fail { border-color:var(--bad); color:#ffb4ae; font-size:17px; }
  .summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(112px,1fr)); gap:9px; margin-bottom:24px; }
  .stat { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:11px; min-height:76px; } .stat strong { display:block; font-size:24px; color:var(--accent); } .stat span { color:var(--muted); font-size:11px; font-weight:800; letter-spacing:.04em; overflow-wrap:anywhere; }
  .filters { position:sticky; top:0; z-index:5; background:rgba(13,17,23,.96); backdrop-filter:blur(12px); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:18px; }
  .filter-grid { display:grid; grid-template-columns:2fr repeat(3,minmax(130px,1fr)); gap:10px; } label { display:grid; gap:4px; color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.02em; }
  input,select,textarea { width:100%; color:var(--text); background:#0d1117; border:1px solid #3a4552; border-radius:7px; padding:9px; } input:focus,select:focus,textarea:focus { outline:2px solid var(--blue); outline-offset:1px; }
  .checks,.toolbar { display:flex; flex-wrap:wrap; gap:11px; align-items:center; margin-top:12px; } .checks label { display:flex; align-items:center; gap:6px; color:var(--text); font-size:13px; font-weight:500; letter-spacing:0; } .checks input { width:auto; accent-color:var(--accent); }
  button { cursor:pointer; color:#07130a; background:var(--accent); border:0; border-radius:7px; padding:9px 12px; font-weight:800; } button.secondary { color:var(--text); background:var(--panel2); border:1px solid #465363; } button:disabled { cursor:not-allowed; opacity:.45; }
  #resultMeta { margin-left:auto; color:var(--muted); font-size:13px; }.cards { display:grid; gap:14px; }.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }.card-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:13px 16px; background:#1c2330; border-bottom:1px solid var(--line); }.card-head h2 { margin-right:auto; overflow-wrap:anywhere; }
  .badge { display:inline-block; padding:3px 7px; border-radius:999px; border:1px solid #415064; color:var(--blue); background:#15243a; font-size:11px; font-weight:800; letter-spacing:.03em; }.badge.source { color:var(--violet); background:#2a1d3b; }.badge.warn { color:#352700; background:var(--warn); border-color:#d29922; }.badge.decision { color:#07130a; background:var(--accent); border-color:#56b66d; }
  .card-body { padding:16px; }.metadata { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:8px; margin-bottom:15px; }.meta { background:#111722; border:1px solid #283343; border-radius:7px; padding:7px 9px; }.meta b { display:block; color:var(--muted); font-size:10px; letter-spacing:.05em; }.meta span { overflow-wrap:anywhere; }
  .warnings { display:flex; flex-wrap:wrap; gap:7px; margin:0 0 14px; }.warnings:empty { display:none; }.section-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }.section { background:#111722; border:1px solid #283343; border-radius:8px; padding:11px; min-width:0; }.section.wide { grid-column:1 / -1; }.section h3 { color:var(--blue); font-size:11px; letter-spacing:.07em; margin:0 0 7px; }.section p { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; }.quote { color:#d6e5f3; font-size:16px; }.empty { color:var(--muted); font-style:italic; }.list { margin:0; padding-left:18px; }.list li + li { margin-top:4px; }.candidate { border-top:1px solid #283343; padding:8px 0; }.candidate:first-child { border-top:0; padding-top:0; }.candidate strong { color:var(--violet); }.expected { border-color:#356647; background:#112119; }.expected h3 { color:var(--accent); }.expected-line { display:grid; grid-template-columns:130px 1fr; gap:7px; }.expected-line b { color:var(--muted); }
  .review { border-top:1px solid var(--line); margin-top:15px; padding-top:14px; display:grid; grid-template-columns:160px minmax(0,1fr); gap:10px; }.review label { color:var(--text); }.review textarea { min-height:44px; resize:vertical; }.compact .section-grid { grid-template-columns:1fr; }.compact .optional-compact { display:none; }.compact .card-body { padding:11px 16px; }.footer { text-align:center; color:var(--muted); margin:24px 0 6px; }
  @media (max-width:760px) { .shell { padding:14px; }.filter-grid,.section-grid,.review { grid-template-columns:1fr; }.expected-line { grid-template-columns:1fr; gap:0; }.filters { position:static; } #resultMeta { width:100%; margin-left:0; } }
</style>
</head>
<body>
<main class="shell" id="app">
  <h1>PEva Agency Dataset Review</h1>
  <p class="lede">Read-only visual review of the synthetic Agency datasets. Reviews stay in this browser until exported; the training files are never changed.</p>
  <div id="validation" class="validation"></div>
  <section id="summary" class="summary" aria-label="Dataset summary"></section>
  <section class="filters" aria-label="Review filters">
    <div class="filter-grid">
      <label>SEARCH TEXT<input id="search" type="search" placeholder="Search IDs, context, messages, candidates…"></label>
      <label>SOURCE<select id="source"><option value="ALL">ALL</option><option value="v0">V0</option><option value="v1">V1</option></select></label>
      <label>MODE<select id="mode"><option value="ALL">ALL</option><option value="AGENCY_REACTIVE">REACTIVE</option><option value="AGENCY_PROACTIVE">PROACTIVE</option></select></label>
      <label>CATEGORY<select id="category"><option value="ALL">ALL</option></select></label>
      <label>DECISION<select id="decision"><option value="ALL">ALL</option></select></label>
    </div>
    <div class="checks">
      <label><input id="actionSafety" type="checkbox"> only action/safety</label><label><input id="memoryContext" type="checkbox"> only memory/context</label><label><input id="personality" type="checkbox"> only personality</label><label><input id="controlSilence" type="checkbox"> only control/silence decisions</label>
    </div>
    <div class="toolbar"><button id="previous" class="secondary">PREVIOUS</button><button id="next" class="secondary">NEXT</button><button id="view" class="secondary">COMPACT VIEW</button><button id="export">EXPORT REVIEW NOTES</button><span id="resultMeta"></span></div>
  </section>
  <section id="cards" class="cards" aria-live="polite"></section>
  <div class="toolbar" style="justify-content:center; margin-top:20px;"><button id="exportBottom">EXPORT REVIEW NOTES</button></div>
  <p class="footer">Local-only viewer · review data is stored in this browser’s localStorage · no dataset rows are edited.</p>
</main>
<script>
const DATA = __DATA__;
const SUMMARY = __SUMMARY__;
const VALIDATION = __VALIDATION__;
const PAGE_SIZE = 12;
const STORAGE_KEY = 'peva-agency-manual-review-v1';
let page = 0;
let compact = false;
let reviews = loadReviews();
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const display = (value, empty = '—') => value === null || value === undefined || value === '' ? `<span class="empty">${empty}</span>` : escapeHtml(value);
function loadReviews() { try { const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); return saved && typeof saved === 'object' ? saved : {}; } catch (_) { return {}; } }
function saveReviews() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(reviews)); } catch (_) { alert('This browser could not save local review notes. You can still export current notes.'); } }
function target(record) { return record.target || {}; }
function asList(values, empty = 'No entries.') { if (!Array.isArray(values) || !values.length) return `<span class="empty">${empty}</span>`; return `<ul class="list">${values.map(value => `<li>${escapeHtml(typeof value === 'string' ? value : JSON.stringify(value))}</li>`).join('')}</ul>`; }
function candidates(values) { if (!Array.isArray(values) || !values.length) return `<span class="empty">No proactive candidates for this reactive example.</span>`; return values.map(candidate => { const name = candidate.name || candidate.uid || 'Unnamed player'; const details = Object.entries(candidate).filter(([key]) => key !== 'name' && key !== 'uid').map(([key,value]) => `<div><span class="muted">${escapeHtml(key.replaceAll('_',' '))}:</span> ${display(typeof value === 'string' ? value : JSON.stringify(value))}</div>`).join(''); return `<div class="candidate"><strong>${escapeHtml(name)}</strong> <span class="muted">(${escapeHtml(candidate.uid || 'no uid')})</span>${details}</div>`; }).join(''); }
function warningBadges(warnings) { return (warnings || []).map(item => `<span class="badge warn">⚠ ${escapeHtml(item)}</span>`).join(''); }
function card(record) {
  const review = record._review || {}; const expected = target(record); const id = String(record.id); const saved = reviews[id] || {}; const decision = expected.decision || '—';
  const context = asList(record.context, 'No recent context.'); const memories = asList(record.player_memories, 'No player memories.');
  const targetRows = [['Decision', decision], ['Reason', expected.reason_code || '—'], ['Target', expected.target_uid || '—'], ['Message', expected.message ? `“${expected.message}”` : '—']];
  return `<article class="card" data-id="${escapeHtml(id)}"><header class="card-head"><h2># ${escapeHtml(review.number)} &nbsp;|&nbsp; ${escapeHtml(record.mode)} &nbsp;|&nbsp; ${escapeHtml(record.category || 'uncategorized')}</h2><span class="badge source">${escapeHtml(String(review.source_file || '').toUpperCase())}</span><span class="badge decision">${escapeHtml(decision)}</span></header><div class="card-body"><div class="metadata"><div class="meta"><b>ID</b><span>${escapeHtml(id)}</span></div><div class="meta"><b>SOURCE FILE</b><span>${escapeHtml(String(review.source_file || '').toUpperCase())}</span></div><div class="meta"><b>MODE</b><span>${escapeHtml(record.mode)}</span></div><div class="meta"><b>CATEGORY</b><span>${escapeHtml(record.category || '—')}</span></div><div class="meta"><b>DECISION</b><span>${escapeHtml(decision)}</span></div><div class="meta"><b>REASON CODE</b><span>${display(expected.reason_code)}</span></div></div><div class="warnings">${warningBadges(review.warnings)}</div><div class="section-grid"><section class="section"><h3>CONTEXT</h3>${context}</section><section class="section"><h3>PLAYER MEMORIES</h3>${memories}</section><section class="section wide"><h3>INPUT / NEW MESSAGE</h3><p class="quote">${record.mode === 'AGENCY_REACTIVE' ? display(record.input ? `“${record.input}”` : '', 'No reactive input.') : '<span class="empty">Proactive selection: no new incoming message.</span>'}</p></section><section class="section wide optional-compact"><h3>AVAILABLE CANDIDATES</h3>${candidates(record.candidates)}</section>${Array.isArray(record.available_actions) && record.available_actions.length ? `<section class="section optional-compact"><h3>AVAILABLE ACTIONS</h3>${asList(record.available_actions)}</section>` : ''}<section class="section expected ${Array.isArray(record.available_actions) && record.available_actions.length ? '' : 'wide'}"><h3>TARGET / EXPECTED PEVA OUTPUT</h3>${targetRows.map(([label,value]) => `<div class="expected-line"><b>${escapeHtml(label)}</b><span>${display(value)}</span></div>`).join('')}</section></div><div class="review"><label>REVIEW STATUS<select class="review-status" data-review-id="${escapeHtml(id)}"><option value="" ${!saved.status ? 'selected' : ''}>NOT REVIEWED</option><option value="GOOD" ${saved.status === 'GOOD' ? 'selected' : ''}>GOOD</option><option value="BAD" ${saved.status === 'BAD' ? 'selected' : ''}>BAD</option><option value="EDIT" ${saved.status === 'EDIT' ? 'selected' : ''}>EDIT</option><option value="SKIP" ${saved.status === 'SKIP' ? 'selected' : ''}>SKIP</option></select></label><label>LOCAL NOTE<textarea class="review-note" data-review-id="${escapeHtml(id)}" placeholder="Optional review note…">${escapeHtml(saved.note || '')}</textarea></label></div></div></article>`;
}
function selectedValues() { return { search: $('search').value.trim().toLowerCase(), source: $('source').value, mode: $('mode').value, category: $('category').value, decision: $('decision').value, action: $('actionSafety').checked, memory: $('memoryContext').checked, personality: $('personality').checked, control: $('controlSilence').checked }; }
function filtered() { const filters = selectedValues(); return DATA.filter(record => { const review = record._review || {}; const expected = target(record); if (filters.search && !JSON.stringify(record).toLowerCase().includes(filters.search)) return false; if (filters.source !== 'ALL' && review.source_file !== filters.source) return false; if (filters.mode !== 'ALL' && record.mode !== filters.mode) return false; if (filters.category !== 'ALL' && record.category !== filters.category) return false; if (filters.decision !== 'ALL' && expected.decision !== filters.decision) return false; if (filters.action && !review.action_safety) return false; if (filters.memory && !review.memory_context) return false; if (filters.personality && !review.personality) return false; if (filters.control && !review.control_silence) return false; return true; }); }
function updateReview(event) { const id = event.target.dataset.reviewId; const cardNode = event.target.closest('.card'); const status = cardNode.querySelector('.review-status').value; const note = cardNode.querySelector('.review-note').value.trim(); if (status || note) reviews[id] = {status, note}; else delete reviews[id]; saveReviews(); }
function render() { const records = filtered(); const pages = Math.max(1, Math.ceil(records.length / PAGE_SIZE)); page = Math.min(page, pages - 1); const visible = records.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE); $('cards').innerHTML = visible.map(card).join('') || '<div class="validation fail">No examples match these filters.</div>'; $('cards').querySelectorAll('.review-status, .review-note').forEach(node => node.addEventListener('change', updateReview)); $('cards').querySelectorAll('.review-note').forEach(node => node.addEventListener('input', updateReview)); $('app').classList.toggle('compact', compact); $('view').textContent = compact ? 'FULL VIEW' : 'COMPACT VIEW'; $('previous').disabled = page === 0; $('next').disabled = page >= pages - 1; $('resultMeta').textContent = `${records.length} matching · page ${page + 1} / ${pages} · ${Object.keys(reviews).length} locally reviewed`; }
function fillSelect(id, values) { const select = $(id); [...values].sort((a,b) => String(a).localeCompare(String(b))).forEach(value => { const option = document.createElement('option'); option.value = value; option.textContent = value; select.append(option); }); }
function exportReviews() { const items = DATA.filter(record => { const review = reviews[String(record.id)]; return review && (review.status || review.note); }).map(record => ({ id: record.id, source_file: record._review.source_file, mode: record.mode, category: record.category, decision: target(record).decision || null, review: reviews[String(record.id)] })); const payload = { schema_version: 1, exported_from: 'PEva Agency Dataset Review', reviewed_count: items.length, reviews: items }; const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], {type:'application/json'}); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = 'peva_agency_manual_review.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0); }
function initialize() { const validation = $('validation'); validation.className = `validation ${VALIDATION.ok ? 'ok' : 'fail'}`; validation.textContent = VALIDATION.message; Object.entries(SUMMARY).forEach(([label,value]) => { const stat = document.createElement('div'); stat.className = 'stat'; stat.innerHTML = `<strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span>`; $('summary').append(stat); }); fillSelect('category', new Set(DATA.map(record => record.category || 'uncategorized'))); fillSelect('decision', new Set(DATA.map(record => target(record).decision || '—'))); ['search','source','mode','category','decision','actionSafety','memoryContext','personality','controlSilence'].forEach(id => $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { page = 0; render(); })); $('previous').addEventListener('click', () => { page--; render(); }); $('next').addEventListener('click', () => { page++; render(); }); $('view').addEventListener('click', () => { compact = !compact; render(); }); $('export').addEventListener('click', exportReviews); $('exportBottom').addEventListener('click', exportReviews); render(); }
initialize();
</script>
</body>
</html>
"""


def build_review() -> Path:
    rows: list[dict[str, Any]] = []
    for source_name, path in SOURCES:
        rows.extend(_read_jsonl(source_name, path, len(rows) + 1))
    _add_review_metadata(rows)
    source_counts = Counter(str(row["_review"]["source_file"]) for row in rows)
    validation_ok = (
        source_counts["v0"] == EXPECTED_COUNTS["v0"]
        and source_counts["v1"] == EXPECTED_COUNTS["v1"]
        and len(rows) == EXPECTED_COUNTS["total"]
    )
    validation = {
        "ok": validation_ok,
        "message": (
            f"VALIDATED: v0 = {source_counts['v0']} · v1 = {source_counts['v1']} · combined = {len(rows)}"
            if validation_ok
            else f"DATASET COUNT WARNING: expected v0 = 80, v1 = 80, total = 160; found v0 = {source_counts['v0']}, v1 = {source_counts['v1']}, total = {len(rows)}"
        ),
    }
    page = HTML_TEMPLATE.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
    page = page.replace("__SUMMARY__", json.dumps(_summary(rows), ensure_ascii=False))
    page = page.replace("__VALIDATION__", json.dumps(validation, ensure_ascii=False))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    return OUTPUT


def main() -> int:
    output = build_review()
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
