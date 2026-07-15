"""
generate_validation_explorer.py — Generates a self-contained HTML explorer
comparing cosine similarity vs ValidationScore (from work_profiles.json).

No dependency on jobs_db.json LLM scores — works immediately after extraction.

What it shows:
  Dashboard:
    - Scatter: cosine (x) vs ValidationScore (y) — coloured by label
    - Threshold sweep table: at each cosine cutoff, recall/precision vs ValidationScore floor
    - Domain breakdown: FP-heavy domains at a glance

  Drilldown (click any job):
    - Left panel:  scores, domain, seniority, output, label — at-a-glance diagnosis
    - Right panel: Original JD text | Extracted work profile side by side

Usage:
    python generate_validation_explorer.py                    # default threshold 0.475, vs_floor 7
    python generate_validation_explorer.py --threshold 0.525
    python generate_validation_explorer.py --vs-floor 6       # ValidationScore floor for good/bad split

Output:
    validation_explorer.html  (open directly in browser)
"""

import argparse
import json
import os
import sys
import numpy as np

# ── PATHS ─────────────────────────────────────────────────────────────────────

DB_FILE               = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job scraper\jobs_db.json"
WORK_DIR              = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job_scraper-v2"
WORK_EMB_FILE         = WORK_DIR + r"\work_embeddings_large.npy"
WORK_IDX_FILE         = WORK_DIR + r"\work_embed_index_large.json"
PROFILE_WORK_EMB_FILE = WORK_DIR + r"\profile_work_embedding_large.npy"
WORK_PROFILES_FILE    = WORK_DIR + r"\work_profiles.json"
CV_WORK_PROFILE_FILE  = WORK_DIR + r"\cv_work_profile.txt"
OUTPUT_FILE           = WORK_DIR + r"\validation_explorer.html"

JD_SKIP_FIELDS = {
    "apply_url", "apply_url_browse", "fetched_date", "posted_date",
    "job_id", "req_id", "ref_number", "source_id", "display_job_id",
    "company", "locations", "work_type", "type_of_employment",
    "work_location_option", "category", "job_type",
    "similarity_score", "match_reason", "match_gaps",
    "matching_skills", "info_level",
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_text(path):
    if not os.path.exists(path): return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

def parse_field(text, field):
    if not text: return ""
    for line in text.split("\n"):
        if line.strip().startswith(field):
            return line.split(":", 1)[1].strip()
    return ""

def assemble_jd(job):
    parts = []
    for field, val in job.items():
        if field in JD_SKIP_FIELDS or not val: continue
        if isinstance(val, list):
            val = "\n".join(str(v) for v in val if v)
        val = str(val).strip()
        if val:
            parts.append(f"[{field.upper()}]\n{val}")
    return "\n\n".join(parts)

def classify(vs, cosine, vs_floor, threshold):
    good = vs >= vs_floor
    passed = cosine >= threshold
    if good and passed:     return "TP"
    if not good and passed: return "FP"
    if good and not passed: return "FN"
    return "TN"

# ── BUILD DATA ────────────────────────────────────────────────────────────────

def build_data(threshold, vs_floor):
    print("Loading files...")
    for path in [DB_FILE, WORK_EMB_FILE, WORK_IDX_FILE, PROFILE_WORK_EMB_FILE]:
        if not os.path.exists(path):
            print(f"[!] Missing: {path}"); sys.exit(1)

    raw  = load_json(DB_FILE)
    db   = {j["apply_url"]: j for j in raw} if isinstance(raw, list) else raw
    wps  = load_json(WORK_PROFILES_FILE) if os.path.exists(WORK_PROFILES_FILE) else {}
    idx  = load_json(WORK_IDX_FILE)
    mat  = np.load(WORK_EMB_FILE).astype(np.float32)
    prof = np.load(PROFILE_WORK_EMB_FILE).astype(np.float32)
    cv_profile = load_text(CV_WORK_PROFILE_FILE)

    prof_norm = prof / (np.linalg.norm(prof) + 1e-10)
    row_norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
    cosines   = (mat / row_norms) @ prof_norm
    url2cos   = {url: float(cosines[i]) for i, url in enumerate(idx)}

    records = []
    for url, wp in wps.items():
        if url not in url2cos: continue
        if not wp or not wp.strip(): continue  # skip empty profiles

        vs_raw = parse_field(wp, "ValidationScore:")
        try:
            vs = max(1, min(10, int(vs_raw)))
        except (ValueError, TypeError):
            continue  # skip jobs where ValidationScore wasn't extracted

        cos      = url2cos[url]
        job      = db.get(url, {})
        domain   = parse_field(wp, "Domain:")
        seniority= parse_field(wp, "Seniority:")
        output   = parse_field(wp, "Output:")
        work     = parse_field(wp, "Work:")
        label    = classify(vs, cos, vs_floor, threshold)

        records.append({
            "url":       url,
            "company":   job.get("company", ""),
            "title":     job.get("title", ""),
            "cosine":    round(cos, 4),
            "vs":        vs,
            "label":     label,
            "domain":    domain,
            "seniority": seniority,
            "output":    output,
            "work":      work,
            "jd_text":   assemble_jd(job),
            # include old LLM score if available for reference
            "llm_score": job.get("similarity_score", None),
        })

    records.sort(key=lambda x: x["cosine"], reverse=True)

    tp = sum(1 for r in records if r["label"] == "TP")
    fp = sum(1 for r in records if r["label"] == "FP")
    fn = sum(1 for r in records if r["label"] == "FN")
    tn = sum(1 for r in records if r["label"] == "TN")
    good = tp + fn
    kept = tp + fp

    # Domain breakdown
    from collections import defaultdict
    domain_stats = defaultdict(lambda: {"TP":0,"FP":0,"FN":0,"TN":0})
    for r in records:
        domain_stats[r["domain"] or "Unknown"][r["label"]] += 1

    # Threshold sweep vs ValidationScore
    sweep = []
    cos_vals = np.array([r["cosine"] for r in records])
    vs_vals  = np.array([r["vs"]     for r in records])
    total    = len(records)
    good_vs  = int((vs_vals >= vs_floor).sum())

    for thresh in np.arange(0.75, 0.35, -0.025):
        thresh = round(float(thresh), 3)
        mask   = cos_vals >= thresh
        n_kept = int(mask.sum())
        n_hit  = int(((cos_vals >= thresh) & (vs_vals >= vs_floor)).sum())
        recall    = round(n_hit / good_vs * 100, 1) if good_vs else 0
        precision = round(n_hit / n_kept  * 100, 1) if n_kept  else 0
        pct_db    = round(n_kept / total  * 100, 1) if total   else 0
        sweep.append({
            "threshold": thresh,
            "kept": n_kept, "pct_db": pct_db,
            "recall": recall, "precision": precision,
            "hit": n_hit, "good": good_vs,
        })

    meta = {
        "threshold": threshold, "vs_floor": vs_floor,
        "total": total, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "good_vs": good_vs,
        "recall": round(tp/good*100, 1) if good else 0,
        "precision": round(tp/kept*100, 1) if kept else 0,
        "cv_profile": cv_profile,
        "domain_stats": dict(domain_stats),
        "sweep": sweep,
    }

    print(f"  Records with ValidationScore : {total}")
    print(f"  TP {tp}  FP {fp}  FN {fn}  TN {tn}")
    print(f"  Recall {meta['recall']}%  Precision {meta['precision']}%")
    return meta, records

# ── HTML ──────────────────────────────────────────────────────────────────────

def build_html(meta, records):
    data_json = json.dumps({"meta": meta, "records": records}, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JobRadar — Validation Explorer</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Syne:wght@400;700;800&display=swap');
:root{{
  --bg:#08080f; --s1:#0f0f18; --s2:#14141f; --border:#1c1c2e;
  --text:#e0e0f0; --muted:#585878; --dim:#303050;
  --tp:#22c55e; --fp:#f97316; --fn:#ef4444; --tn:#3b82f6;
  --accent:#7c3aed; --cyan:#06b6d4; --yellow:#eab308;
  --tp-bg:rgba(34,197,94,.08); --fp-bg:rgba(249,115,22,.08);
  --fn-bg:rgba(239,68,68,.08); --tn-bg:rgba(59,130,246,.08);
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;height:100vh;overflow:hidden;display:flex;flex-direction:column}}

/* ── HEADER ── */
header{{padding:14px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px;flex-shrink:0}}
.logo{{font-family:'Syne',sans-serif;font-weight:800;font-size:17px}}
.logo span{{color:var(--cyan)}}
.hstat{{background:var(--s1);border:1px solid var(--border);border-radius:5px;padding:4px 10px;font-size:11px;display:flex;gap:8px;align-items:center}}
.hstat b{{font-size:13px}}
.dot{{width:7px;height:7px;border-radius:50%;flex-shrink:0}}

/* ── TABS ── */
.tabs{{display:flex;border-bottom:1px solid var(--border);flex-shrink:0;background:var(--s1)}}
.tab{{padding:10px 20px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.05em;color:var(--muted);border-bottom:2px solid transparent;transition:all .15s}}
.tab:hover{{color:var(--text)}}
.tab.active{{color:var(--cyan);border-bottom-color:var(--cyan)}}

/* ── MAIN LAYOUT ── */
.view{{display:none;flex:1;overflow:hidden}}
.view.active{{display:flex}}

/* ── DASHBOARD VIEW ── */
#v-dash{{flex-direction:column;overflow-y:auto;padding:20px 24px;gap:20px}}
.dash-top{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.panel{{background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:16px}}
.panel-title{{font-family:'Syne',sans-serif;font-size:12px;font-weight:700;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;margin-bottom:12px}}
canvas{{border-radius:4px}}

/* sweep table */
.sweep-table{{width:100%;border-collapse:collapse;font-size:11px}}
.sweep-table th{{padding:6px 10px;text-align:left;color:var(--muted);font-size:10px;letter-spacing:.06em;border-bottom:1px solid var(--border);white-space:nowrap}}
.sweep-table td{{padding:6px 10px;border-bottom:1px solid rgba(255,255,255,.03);white-space:nowrap}}
.sweep-table tr:hover td{{background:rgba(255,255,255,.02)}}
.sweep-table tr.recommended td{{background:rgba(6,182,212,.06);}}
.rec-tag{{color:var(--cyan);font-size:9px;margin-left:6px;font-weight:700}}
.g{{color:var(--tp)}} .a{{color:var(--yellow)}} .r{{color:var(--fn)}}

/* domain table */
.dtable{{width:100%;border-collapse:collapse;font-size:11px}}
.dtable th{{padding:6px 10px;text-align:left;color:var(--muted);font-size:10px;letter-spacing:.06em;border-bottom:1px solid var(--border)}}
.dtable td{{padding:6px 10px;border-bottom:1px solid rgba(255,255,255,.03)}}
.dtable tr:hover td{{background:rgba(255,255,255,.02)}}
.fpbar{{height:6px;background:var(--border);border-radius:3px;overflow:hidden;width:80px}}
.fpfill{{height:100%;border-radius:3px;background:var(--fp)}}

/* ── EXPLORER VIEW ── */
#v-explore{{flex-direction:row}}
.job-list{{width:320px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column}}
.list-controls{{padding:10px 12px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:6px;flex-shrink:0}}
.list-controls input,.list-controls select{{background:var(--s2);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:5px 8px;font-family:'JetBrains Mono',monospace;font-size:11px;outline:none;width:100%}}
.list-controls input:focus,.list-controls select:focus{{border-color:var(--accent)}}
.list-scroll{{overflow-y:auto;flex:1}}
.job-item{{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.03);cursor:pointer;transition:background .1s}}
.job-item:hover{{background:var(--s2)}}
.job-item.selected{{background:rgba(124,58,237,.12);border-left:2px solid var(--accent)}}
.ji-top{{display:flex;align-items:center;gap:6px;margin-bottom:4px}}
.ji-title{{font-size:11px;color:var(--text);line-height:1.4;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ji-company{{font-size:10px;color:var(--cyan);text-transform:uppercase;letter-spacing:.04em}}
.ji-scores{{display:flex;gap:8px;margin-top:4px;font-size:10px;color:var(--muted)}}
.count-tag{{background:var(--border);border-radius:8px;padding:1px 6px;font-size:10px;color:var(--muted);margin-left:auto}}

/* detail panel */
.detail{{flex:1;display:flex;overflow:hidden}}
.detail-empty{{flex:1;display:flex;align-items:center;justify-content:center;color:var(--muted);flex-direction:column;gap:8px}}
.detail-empty .icon{{font-size:32px}}

/* left pane — diagnosis */
.d-left{{width:280px;flex-shrink:0;border-right:1px solid var(--border);overflow-y:auto;padding:20px 16px;display:flex;flex-direction:column;gap:16px}}
.diag-card{{background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:14px}}
.diag-title{{font-family:'Syne',sans-serif;font-size:15px;font-weight:700;line-height:1.3;margin-bottom:6px}}
.diag-company{{font-size:10px;color:var(--cyan);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}}
.score-row{{display:flex;gap:8px;margin-bottom:12px}}
.score-box{{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px 8px;text-align:center}}
.score-box .sv{{font-family:'Syne',sans-serif;font-size:22px;font-weight:800;line-height:1}}
.score-box .sl{{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-top:3px}}
.field-row{{display:flex;justify-content:space-between;align-items:flex-start;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04);gap:8px}}
.field-row:last-child{{border-bottom:none}}
.fl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;flex-shrink:0}}
.fv{{font-size:11px;color:var(--text);text-align:right;line-height:1.4}}
.diagnosis-box{{background:var(--bg);border-radius:6px;padding:10px 12px;border-left:3px solid var(--fp)}}
.diagnosis-box.good{{border-left-color:var(--tp)}}
.diagnosis-box.warn{{border-left-color:var(--yellow)}}
.diag-label{{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}}
.diag-text{{font-size:11px;line-height:1.6;color:var(--text)}}

/* right pane — text comparison */
.d-right{{flex:1;overflow:hidden;display:flex;flex-direction:column}}
.text-tabs{{display:flex;border-bottom:1px solid var(--border);flex-shrink:0}}
.text-tab{{padding:8px 16px;cursor:pointer;font-size:11px;color:var(--muted);border-bottom:2px solid transparent;transition:all .15s}}
.text-tab:hover{{color:var(--text)}}
.text-tab.active{{color:var(--cyan);border-bottom-color:var(--cyan)}}
.text-pane{{display:none;flex:1;overflow-y:auto;padding:16px 20px}}
.text-pane.active{{display:block}}
.text-pane pre{{white-space:pre-wrap;word-break:break-word;font-size:11px;line-height:1.8;color:var(--muted);font-family:'JetBrains Mono',monospace}}
.wp-block{{margin-bottom:12px}}
.wp-label{{font-size:9px;font-weight:700;letter-spacing:.1em;color:var(--cyan);text-transform:uppercase;margin-bottom:4px}}
.wp-text{{font-size:11px;line-height:1.7;color:var(--text)}}

/* badges */
.lb{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700}}
.lb.TP{{background:var(--tp-bg);color:var(--tp);border:1px solid rgba(34,197,94,.25)}}
.lb.FP{{background:var(--fp-bg);color:var(--fp);border:1px solid rgba(249,115,22,.25)}}
.lb.FN{{background:var(--fn-bg);color:var(--fn);border:1px solid rgba(239,68,68,.25)}}
.lb.TN{{background:var(--tn-bg);color:var(--tn);border:1px solid rgba(59,130,246,.25)}}

::-webkit-scrollbar{{width:4px;height:4px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:var(--dim);border-radius:2px}}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">Job<span>Radar</span> <span style="color:var(--muted);font-weight:400;font-size:12px">/ Validation Explorer</span></div>
    <div style="font-size:10px;color:var(--muted);margin-top:2px">cosine vs ValidationScore · threshold {meta['threshold']} · vs floor ≥{meta['vs_floor']}</div>
  </div>
  <div class="hstat"><div class="dot" style="background:var(--tp)"></div>TP<b id="h-tp">{meta['tp']}</b></div>
  <div class="hstat"><div class="dot" style="background:var(--fp)"></div>FP<b id="h-fp">{meta['fp']}</b></div>
  <div class="hstat"><div class="dot" style="background:var(--fn)"></div>FN<b id="h-fn">{meta['fn']}</b></div>
  <div class="hstat"><div class="dot" style="background:var(--tn)"></div>TN<b id="h-tn">{meta['tn']}</b></div>
  <div class="hstat" style="margin-left:8px">Recall<b style="color:var(--tp)">{meta['recall']}%</b></div>
  <div class="hstat">Precision<b style="color:var(--fp)">{meta['precision']}%</b></div>
  <div style="margin-left:auto;font-size:10px;color:var(--muted)">{meta['total']} jobs with ValidationScore</div>
</header>

<div class="tabs">
  <div class="tab active" onclick="showTab('dash',this)">📊 Dashboard</div>
  <div class="tab" onclick="showTab('explore',this)">🔍 Job Explorer</div>
</div>

<!-- ── DASHBOARD ── -->
<div id="v-dash" class="view active">
  <div class="dash-top">
    <div class="panel">
      <div class="panel-title">Cosine vs ValidationScore Scatter</div>
      <canvas id="scatter" height="260"></canvas>
    </div>
    <div class="panel">
      <div class="panel-title">Threshold Sweep (cosine vs VS ≥{meta['vs_floor']})</div>
      <div style="overflow-y:auto;max-height:260px">
        <table class="sweep-table">
          <thead><tr>
            <th>Threshold</th><th>Kept</th><th>% DB</th>
            <th>Recall</th><th>Precision</th><th>Hit/Good</th>
          </tr></thead>
          <tbody id="sweep-body"></tbody>
        </table>
      </div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-title">Domain Breakdown — FP vs TP at threshold {meta['threshold']}</div>
    <table class="dtable" id="domain-table">
      <thead><tr>
        <th>Domain</th><th>Total</th><th>TP</th><th>FP</th><th>FN</th><th>TN</th>
        <th>Recall</th><th>FP rate</th><th>FP volume</th>
      </tr></thead>
      <tbody id="domain-body"></tbody>
    </table>
  </div>
  <div class="panel" id="cv-panel" style="display:none">
    <div class="panel-title">Your CV Work Profile (comparison baseline)</div>
    <div style="font-size:11px;line-height:1.8;color:var(--text)" id="cv-text"></div>
  </div>
</div>

<!-- ── EXPLORER ── -->
<div id="v-explore" class="view">
  <div class="job-list">
    <div class="list-controls">
      <input type="text" id="search" placeholder="Search title, company, domain..." oninput="filterJobs()">
      <div style="display:flex;gap:6px">
        <select id="f-label" onchange="filterJobs()" style="flex:1">
          <option value="ALL">All labels</option>
          <option value="TP">TP</option><option value="FP">FP</option>
          <option value="FN">FN</option><option value="TN">TN</option>
        </select>
        <select id="f-domain" onchange="filterJobs()" style="flex:1">
          <option value="ALL">All domains</option>
        </select>
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <select id="f-sort" onchange="filterJobs()" style="flex:1">
          <option value="cos_d">Cosine ↓</option>
          <option value="cos_a">Cosine ↑</option>
          <option value="vs_d">VS ↓</option>
          <option value="vs_a">VS ↑</option>
        </select>
        <span class="count-tag" id="list-count">—</span>
      </div>
    </div>
    <div class="list-scroll" id="list-scroll"></div>
  </div>

  <div class="detail" id="detail">
    <div class="detail-empty" id="detail-empty">
      <div class="icon">⬡</div>
      <div style="color:var(--muted);font-size:11px">Select a job to inspect</div>
    </div>
    <div id="detail-content" style="display:none;flex:1;overflow:hidden;display:none;flex-direction:row">
      <!-- left: diagnosis -->
      <div class="d-left" id="d-left"></div>
      <!-- right: text comparison -->
      <div class="d-right">
        <div class="text-tabs">
          <div class="text-tab active" onclick="showTextTab('wp',this)">Extracted Profile</div>
          <div class="text-tab" onclick="showTextTab('jd',this)">Original JD</div>
          <div class="text-tab" onclick="showTextTab('cv',this)">Your CV Profile</div>
        </div>
        <div class="text-pane active" id="tp-wp"></div>
        <div class="text-pane" id="tp-jd"></div>
        <div class="text-pane" id="tp-cv"></div>
      </div>
    </div>
  </div>
</div>

<script>
const D = {data_json};
const META = D.meta;
const ALL  = D.records;
let filtered = [...ALL];
let selectedIdx = -1;

// ── INIT ──────────────────────────────────────────────────────────────────────
initDashboard();
initExplorer();

function initDashboard() {{
  drawScatter();
  buildSweepTable();
  buildDomainTable();
  if (META.cv_profile) {{
    document.getElementById('cv-panel').style.display = 'block';
    document.getElementById('cv-text').textContent = META.cv_profile;
  }}
}}

function initExplorer() {{
  const domains = [...new Set(ALL.map(r=>r.domain||'Unknown'))].sort();
  const sel = document.getElementById('f-domain');
  domains.forEach(d=>{{ const o=document.createElement('option');o.value=o.textContent=d;sel.appendChild(o); }});
  filterJobs();
}}

// ── TABS ──────────────────────────────────────────────────────────────────────
function showTab(id, el) {{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('v-'+id).classList.add('active');
  if (id==='dash') setTimeout(drawScatter, 50);
}}

function showTextTab(id, el) {{
  document.querySelectorAll('.text-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.text-pane').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tp-'+id).classList.add('active');
}}

// ── SCATTER ───────────────────────────────────────────────────────────────────
function drawScatter() {{
  const canvas = document.getElementById('scatter');
  if (!canvas) return;
  canvas.width = canvas.parentElement.clientWidth - 32;
  const ctx = canvas.getContext('2d');
  const W=canvas.width, H=canvas.height;
  const P={{t:16,r:16,b:28,l:36}};
  ctx.fillStyle='#0a0a12'; ctx.fillRect(0,0,W,H);

  const cos_vals = ALL.map(r=>r.cosine);
  const minC=Math.min(...cos_vals), maxC=Math.max(...cos_vals);
  const toX = c => P.l+(c-minC)/(maxC-minC+.001)*(W-P.l-P.r);
  const toY = s => P.t+(1-(s-1)/9)*(H-P.t-P.b);

  // Grid
  ctx.strokeStyle='#1c1c2e'; ctx.lineWidth=1;
  for(let s=1;s<=10;s++){{
    const y=toY(s);
    ctx.beginPath();ctx.moveTo(P.l,y);ctx.lineTo(W-P.r,y);ctx.stroke();
    ctx.fillStyle='#383858';ctx.font='9px monospace';ctx.fillText(s,3,y+3);
  }}

  // Threshold line
  const tx=toX(META.threshold);
  ctx.strokeStyle='rgba(124,58,237,.7)';ctx.setLineDash([4,3]);
  ctx.beginPath();ctx.moveTo(tx,P.t);ctx.lineTo(tx,H-P.b);ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle='rgba(124,58,237,.9)';ctx.font='9px monospace';
  ctx.fillText('cos≥'+META.threshold,tx+3,P.t+10);

  // VS floor line
  const vy=toY(META.vs_floor);
  ctx.strokeStyle='rgba(6,182,212,.5)';ctx.setLineDash([4,3]);
  ctx.beginPath();ctx.moveTo(P.l,vy);ctx.lineTo(W-P.r,vy);ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle='rgba(6,182,212,.8)';ctx.font='9px monospace';
  ctx.fillText('VS≥'+META.vs_floor,P.l+2,vy-4);

  const C={{TP:'#22c55e',FP:'#f97316',FN:'#ef4444',TN:'#3b82f6'}};
  ALL.forEach(r=>{{
    ctx.beginPath();
    ctx.arc(toX(r.cosine),toY(r.vs),2.5,0,Math.PI*2);
    ctx.fillStyle=C[r.label]+'99';ctx.fill();
  }});

  // Legend
  let lx=P.l; const ly=H-6;
  Object.entries(C).forEach(([lbl,col])=>{{
    ctx.beginPath();ctx.arc(lx+4,ly-2,4,0,Math.PI*2);
    ctx.fillStyle=col;ctx.fill();
    ctx.fillStyle='#888';ctx.font='9px monospace';
    ctx.fillText(lbl,lx+10,ly);lx+=36;
  }});
  ctx.fillStyle='#555';ctx.fillText('cosine →',W-55,H-4);
  ctx.fillText('VS ↑',2,P.t-2);
}}

// ── SWEEP TABLE ───────────────────────────────────────────────────────────────
function buildSweepTable() {{
  const tbody = document.getElementById('sweep-body');
  let recommended = false;
  tbody.innerHTML = META.sweep.map(row=>{{
    const rc = row.recall>=95?'g':row.recall>=80?'a':'r';
    const isRec = !recommended && row.recall>=95;
    if(isRec) recommended=true;
    return `<tr class="${{isRec?'recommended':''}}">
      <td>${{row.threshold.toFixed(3)}}${{isRec?'<span class="rec-tag">★ REC</span>':''}}</td>
      <td>${{row.kept}}</td>
      <td>${{row.pct_db}}%</td>
      <td class="${{rc}}">${{row.recall}}%</td>
      <td>${{row.precision}}%</td>
      <td>${{row.hit}}/${{row.good}}</td>
    </tr>`;
  }}).join('');
}}

// ── DOMAIN TABLE ──────────────────────────────────────────────────────────────
function buildDomainTable() {{
  const tbody = document.getElementById('domain-body');
  const stats = META.domain_stats;
  const rows = Object.entries(stats).map(([domain,s])=>{{
    const total = s.TP+s.FP+s.FN+s.TN;
    const good  = s.TP+s.FN;
    const kept  = s.TP+s.FP;
    const recall = good ? (s.TP/good*100).toFixed(0)+'%' : '—';
    const fprate = kept ? (s.FP/kept*100).toFixed(0)+'%' : '—';
    return {{domain,total,tp:s.TP,fp:s.FP,fn:s.FN,tn:s.TN,recall,fprate,fpn:s.FP}};
  }}).sort((a,b)=>b.fpn-a.fpn);

  const maxFP = Math.max(...rows.map(r=>r.fpn),1);
  tbody.innerHTML = rows.map(r=>{{
    const pct = Math.round(r.fpn/maxFP*100);
    return `<tr>
      <td>${{e(r.domain)}}</td>
      <td style="color:var(--muted)">${{r.total}}</td>
      <td style="color:var(--tp)">${{r.tp}}</td>
      <td style="color:var(--fp)">${{r.fp}}</td>
      <td style="color:var(--fn)">${{r.fn}}</td>
      <td style="color:var(--tn)">${{r.tn}}</td>
      <td class="${{parseInt(r.recall)>=90?'g':parseInt(r.recall)>=70?'a':'r'}}">${{r.recall}}</td>
      <td class="${{parseInt(r.fprate)>=50?'r':parseInt(r.fprate)>=25?'a':'g'}}">${{r.fprate}}</td>
      <td><div class="fpbar"><div class="fpfill" style="width:${{pct}}%"></div></div></td>
    </tr>`;
  }}).join('');
}}

// ── EXPLORER ──────────────────────────────────────────────────────────────────
function filterJobs() {{
  const q  = document.getElementById('search').value.toLowerCase();
  const fl = document.getElementById('f-label').value;
  const fd = document.getElementById('f-domain').value;
  const so = document.getElementById('f-sort').value;

  filtered = ALL.filter(r=>{{
    if(fl!=='ALL'&&r.label!==fl) return false;
    if(fd!=='ALL'&&(r.domain||'Unknown')!==fd) return false;
    if(q){{
      const h=`${{r.company}} ${{r.title}} ${{r.domain}} ${{r.seniority}} ${{r.work}}`.toLowerCase();
      if(!h.includes(q)) return false;
    }}
    return true;
  }});

  filtered.sort((a,b)=>{{
    if(so==='cos_d') return b.cosine-a.cosine;
    if(so==='cos_a') return a.cosine-b.cosine;
    if(so==='vs_d')  return b.vs-a.vs;
    if(so==='vs_a')  return a.vs-b.vs;
    return 0;
  }});

  document.getElementById('list-count').textContent = filtered.length+' jobs';
  renderList();
}}

function renderList() {{
  const el = document.getElementById('list-scroll');
  el.innerHTML = filtered.map((r,i)=>{{
    const sc = r.vs>=7?'var(--tp)':r.vs>=5?'var(--yellow)':'var(--fn)';
    const cc = cosColor(r.cosine);
    return `<div class="job-item${{selectedIdx===i?' selected':''}}" onclick="selectJob(${{i}})">
      <div class="ji-top">
        <span class="lb ${{r.label}}">${{r.label}}</span>
        <span style="font-size:10px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{e(r.domain||'Unknown')}}</span>
      </div>
      <div class="ji-title">${{e(r.title)}}</div>
      <div class="ji-company">${{e(r.company)}}</div>
      <div class="ji-scores">
        <span>VS <b style="color:${{sc}}">${{r.vs}}</b></span>
        <span>cos <b style="color:${{cc}}">${{r.cosine.toFixed(3)}}</b></span>
        <span style="color:var(--dim)">${{e(r.seniority)}}</span>
      </div>
    </div>`;
  }}).join('');
}}

function cosColor(c) {{
  if(c>=0.65) return 'var(--tp)';
  if(c>=0.55) return 'var(--yellow)';
  return 'var(--fn)';
}}

function selectJob(i) {{
  selectedIdx = i;
  renderList();
  const r = filtered[i];
  if(!r) return;

  document.getElementById('detail-empty').style.display='none';
  const dc = document.getElementById('detail-content');
  dc.style.display='flex';

  // ── Diagnosis card ────────────────────────────────────────────────────────
  const sc  = r.vs>=7?'var(--tp)':r.vs>=5?'var(--yellow)':'var(--fn)';
  const cc  = cosColor(r.cosine);
  const diag = getDiagnosis(r);

  document.getElementById('d-left').innerHTML = `
    <div class="diag-card">
      <div class="diag-company">${{e(r.company)}}</div>
      <div class="diag-title">${{e(r.title)}}</div>
      <div style="margin-bottom:10px"><span class="lb ${{r.label}}">${{r.label}}</span></div>
      <div class="score-row">
        <div class="score-box">
          <div class="sv" style="color:${{sc}}">${{r.vs}}</div>
          <div class="sl">VS Score</div>
        </div>
        <div class="score-box">
          <div class="sv" style="color:${{cc}}">${{r.cosine.toFixed(3)}}</div>
          <div class="sl">Cosine</div>
        </div>
        ${{r.llm_score!=null?`<div class="score-box"><div class="sv" style="color:var(--muted)">${{r.llm_score}}</div><div class="sl">LLM ref</div></div>`:''}}
      </div>
      <div class="field-row"><span class="fl">Domain</span><span class="fv">${{e(r.domain||'—')}}</span></div>
      <div class="field-row"><span class="fl">Seniority</span><span class="fv">${{e(r.seniority||'—')}}</span></div>
      <div class="field-row"><span class="fl">Output</span><span class="fv" style="color:var(--cyan)">${{e(r.output||'—')}}</span></div>
    </div>
    <div class="diagnosis-box ${{diag.cls}}">
      <div class="diag-label">⚡ Diagnosis</div>
      <div class="diag-text">${{diag.text}}</div>
    </div>
    <div style="padding:4px 0">
      <a href="${{r.url}}" target="_blank" style="color:var(--cyan);font-size:10px;text-decoration:none;word-break:break-all">↗ Apply link</a>
    </div>
  `;

  // ── Text panels ───────────────────────────────────────────────────────────
  document.getElementById('tp-wp').innerHTML = `
    ${{r.seniority?`<div class="wp-block"><div class="wp-label">Seniority</div><div class="wp-text">${{e(r.seniority)}}</div></div>`:''}}
    ${{r.domain?`<div class="wp-block"><div class="wp-label">Domain</div><div class="wp-text">${{e(r.domain)}}</div></div>`:''}}
    ${{r.output?`<div class="wp-block"><div class="wp-label">Output — what this person produces</div><div class="wp-text" style="color:var(--cyan)">${{e(r.output)}}</div></div>`:''}}
    ${{r.work?`<div class="wp-block"><div class="wp-label">Work — day-to-day activities</div><div class="wp-text">${{e(r.work)}}</div></div>`:'<div style="color:var(--muted);padding:16px">No work profile extracted</div>'}}
  `;
  document.getElementById('tp-jd').innerHTML = r.jd_text
    ? `<pre>${{e(r.jd_text)}}</pre>`
    : '<div style="color:var(--muted);padding:16px">No JD text available</div>';
  document.getElementById('tp-cv').innerHTML = META.cv_profile
    ? `<div class="wp-text" style="line-height:1.9">${{e(META.cv_profile)}}</div>`
    : '<div style="color:var(--muted);padding:16px">cv_work_profile.txt not found</div>';

  // Reset to first text tab
  document.querySelectorAll('.text-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.text-pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.text-tab')[0].classList.add('active');
  document.getElementById('tp-wp').classList.add('active');
}}

function getDiagnosis(r) {{
  // Logic: explain WHY this job is TP/FP/FN/TN at a glance
  const domainOk = r.domain && (
    r.domain.includes('Analytics') ||
    r.domain.includes('Data Science') ||
    r.domain.includes('Data Engineering')
  );
  const vsHigh   = r.vs >= 7;
  const cosHigh  = r.cosine >= META.threshold;

  if(r.label==='TP') return {{
    cls:'good',
    text:`Strong match. Domain: ${{r.domain}}. VS ${{r.vs}} + cosine ${{r.cosine.toFixed(3)}} both above threshold. Embedding and extraction agree.`
  }};
  if(r.label==='FP') {{
    if(!domainOk) return {{
      cls:'',
      text:`Domain mismatch — "${{r.domain}}" passed the cosine filter but VS ${{r.vs}} is below floor. The embedding saw analytical vocabulary but the domain is wrong.`
    }};
    if(r.vs<=3) return {{
      cls:'',
      text:`Strong domain rejection (VS ${{r.vs}}). Extraction correctly flagged low fit but cosine ${{r.cosine.toFixed(3)}} still passed. Check Output field — consumer vs builder mismatch?`
    }};
    return {{
      cls:'',
      text:`Borderline case — cosine ${{r.cosine.toFixed(3)}} passed but VS ${{r.vs}} below floor ${{META.vs_floor}}. Review Output and Work fields for mismatch.`
    }};
  }}
  if(r.label==='FN') return {{
    cls:'warn',
    text:`Recall miss — VS ${{r.vs}} says good job but cosine ${{r.cosine.toFixed(3)}} below threshold. Work profile may be poorly extracted or embedding signal too weak for this role type.`
  }};
  return {{
    cls:'good',
    text:`Both agree low fit. VS ${{r.vs}}, cosine ${{r.cosine.toFixed(3)}}. Safe to skip.`
  }};
}}

function e(s){{
  if(!s&&s!==0)return'';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

document.addEventListener('keydown',ev=>{{
  if(ev.key==='ArrowDown'){{ev.preventDefault();if(selectedIdx<filtered.length-1)selectJob(selectedIdx+1);}}
  if(ev.key==='ArrowUp'){{ev.preventDefault();if(selectedIdx>0)selectJob(selectedIdx-1);}}
}});
window.addEventListener('resize',()=>drawScatter());
</script>
</body>
</html>"""

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate self-contained validation explorer HTML")
    parser.add_argument("--threshold", type=float, default=0.475)
    parser.add_argument("--vs-floor",  type=int,   default=7,
                        help="ValidationScore floor for good/bad split (default 7)")
    args = parser.parse_args()

    meta, records = build_data(args.threshold, args.vs_floor)
    html = build_html(meta, records)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n  ✅ Saved → {OUTPUT_FILE}")
    print(f"  Open directly in browser — no server needed.\n")

if __name__ == "__main__":
    main()