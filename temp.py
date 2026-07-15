import json

WORK_PROFILES_FILE = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job_scraper-v2\work_profiles.json"

with open(WORK_PROFILES_FILE, encoding="utf-8") as f:
    profiles = json.load(f)

empty = [url for url, text in profiles.items() if not text or not text.strip()]
print(f"Empty: {len(empty)}")

# Delete empty entries so extract_work_profiles.py picks them up again
for url in empty:
    del profiles[url]

with open(WORK_PROFILES_FILE, "w", encoding="utf-8") as f:
    json.dump(profiles, f, indent=2, ensure_ascii=False)

print(f"Removed {len(empty)} empty entries — ready for re-extraction.")

# # """
# # dump_tp_fp.py — Dumps TP and FP samples to a readable txt file for analysis.

# # Outputs a structured txt file with:
# #   - Your CV work profile (once at top)
# #   - N True Positives (LLM >= floor AND cosine >= threshold)
# #   - N False Positives (LLM < floor AND cosine >= threshold)

# # Each job entry shows:
# #   - Company, title, LLM score, cosine, seniority
# #   - Extracted work profile
# #   - Full JD text (all content fields)
# #   - LLM reason + gaps

# # Usage:
# #     python dump_tp_fp.py                        # 50 TP + 50 FP, default threshold
# #     python dump_tp_fp.py --n 30                 # 30 each
# #     python dump_tp_fp.py --threshold 0.525      # custom threshold
# #     python dump_tp_fp.py --llm-floor 7          # good-job bar
# #     python dump_tp_fp.py --sort cosine          # sort FPs by cosine desc (default)
# #     python dump_tp_fp.py --sort llm             # sort FPs by LLM score desc

# # Output:
# #     tp_fp_analysis.txt in WORK_DIR
# # """

# # import argparse
# # import json
# # import os
# # import sys
# # import numpy as np
# # from textwrap import fill

# # # ── PATHS ─────────────────────────────────────────────────────────────────────

# # DB_FILE               = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job scraper\jobs_db.json"
# # WORK_DIR              = r"C:\Users\Thanmaye Majeti\OneDrive\Desktop\Job_scraper-v2"
# # WORK_EMB_FILE         = WORK_DIR + r"\work_embeddings_large.npy"
# # WORK_IDX_FILE         = WORK_DIR + r"\work_embed_index_large.json"
# # PROFILE_WORK_EMB_FILE = WORK_DIR + r"\profile_work_embedding_large.npy"
# # WORK_PROFILES_FILE    = WORK_DIR + r"\work_profiles.json"
# # CV_WORK_PROFILE_FILE  = WORK_DIR + r"\cv_work_profile.txt"
# # OUTPUT_FILE           = WORK_DIR + r"\tp_fp_analysis.txt"

# # JD_SKIP_FIELDS = {
# #     "apply_url", "apply_url_browse", "fetched_date", "posted_date",
# #     "job_id", "req_id", "ref_number", "source_id", "display_job_id",
# #     "company", "locations", "work_type", "type_of_employment",
# #     "work_location_option", "category", "job_type",
# #     "similarity_score", "match_reason", "match_gaps",
# #     "matching_skills", "info_level",
# # }

# # # ── HELPERS ───────────────────────────────────────────────────────────────────

# # def load_json(path):
# #     with open(path, "r", encoding="utf-8") as f:
# #         return json.load(f)

# # def load_text(path):
# #     if not os.path.exists(path):
# #         return ""
# #     with open(path, "r", encoding="utf-8") as f:
# #         return f.read().strip()

# # def parse_seniority(text):
# #     if not text: return "Unknown"
# #     for line in text.split("\n"):
# #         if line.strip().startswith("Seniority:"):
# #             return line.replace("Seniority:", "").strip()
# #     return "Unknown"

# # def parse_work(text):
# #     if not text: return ""
# #     for line in text.split("\n"):
# #         if line.strip().startswith("Work:"):
# #             return line.replace("Work:", "").strip()
# #     return text.strip()

# # def assemble_jd(job):
# #     parts = []
# #     for field, val in job.items():
# #         if field in JD_SKIP_FIELDS or not val:
# #             continue
# #         if isinstance(val, list):
# #             val = "\n".join(str(v) for v in val if v)
# #         val = str(val).strip()
# #         if val:
# #             parts.append(f"  [{field.upper()}]\n  {val}")
# #     return "\n\n".join(parts)

# # def divider(char="─", width=72):
# #     return char * width

# # def section(title, char="═", width=72):
# #     pad = max(0, width - len(title) - 4)
# #     return f"{char*2} {title} {char * pad}"

# # # ── MAIN ──────────────────────────────────────────────────────────────────────

# # def main():
# #     parser = argparse.ArgumentParser(description="Dump TP/FP samples to txt for analysis")
# #     parser.add_argument("--n",          type=int,   default=50,   help="Samples per bucket (default 50)")
# #     parser.add_argument("--threshold",  type=float, default=0.475,help="Cosine threshold (default 0.475)")
# #     parser.add_argument("--llm-floor",  type=int,   default=7,    help="LLM good-job floor (default 7)")
# #     parser.add_argument("--sort",       default="cosine",          help="Sort FPs by: cosine (default) or llm")
# #     args = parser.parse_args()

# #     # Load everything
# #     print("Loading...")
# #     for path in [DB_FILE, WORK_EMB_FILE, WORK_IDX_FILE, PROFILE_WORK_EMB_FILE]:
# #         if not os.path.exists(path):
# #             print(f"[!] Missing: {path}"); sys.exit(1)

# #     raw  = load_json(DB_FILE)
# #     db   = {j["apply_url"]: j for j in raw} if isinstance(raw, list) else raw
# #     wps  = load_json(WORK_PROFILES_FILE) if os.path.exists(WORK_PROFILES_FILE) else {}
# #     idx  = load_json(WORK_IDX_FILE)
# #     mat  = np.load(WORK_EMB_FILE).astype(np.float32)
# #     prof = np.load(PROFILE_WORK_EMB_FILE).astype(np.float32)
# #     cv_profile = load_text(CV_WORK_PROFILE_FILE)

# #     # Cosines
# #     prof_norm = prof / (np.linalg.norm(prof) + 1e-10)
# #     row_norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
# #     cosines   = (mat / row_norms) @ prof_norm
# #     url2cos   = {url: float(cosines[i]) for i, url in enumerate(idx)}

# #     # Build candidate lists
# #     tps, fps = [], []
# #     for url, job in db.items():
# #         llm = job.get("similarity_score")
# #         if llm is None or url not in url2cos:
# #             continue
# #         try:
# #             llm_int = max(1, min(10, int(round(float(llm)))))
# #         except (ValueError, TypeError):
# #             continue

# #         cos   = url2cos[url]
# #         wp    = wps.get(url, "")
# #         entry = {
# #             "url":          url,
# #             "company":      job.get("company", ""),
# #             "title":        job.get("title", ""),
# #             "llm_score":    llm_int,
# #             "cosine":       cos,
# #             "seniority":    parse_seniority(wp),
# #             "work_profile": parse_work(wp),
# #             "jd_text":      assemble_jd(job),
# #             "reason":       job.get("match_reason", ""),
# #             "gaps":         job.get("match_gaps", ""),
# #             "skills":       job.get("matching_skills", []),
# #         }

# #         good_llm = llm_int >= args.llm_floor
# #         pass_emb = cos >= args.threshold

# #         if good_llm and pass_emb:
# #             tps.append(entry)
# #         elif not good_llm and pass_emb:
# #             fps.append(entry)

# #     # Sort
# #     tps.sort(key=lambda x: x["cosine"], reverse=True)
# #     if args.sort == "llm":
# #         fps.sort(key=lambda x: x["llm_score"], reverse=True)
# #     else:
# #         fps.sort(key=lambda x: x["cosine"], reverse=True)

# #     tp_sample = tps[:args.n]
# #     fp_sample = fps[:args.n]

# #     print(f"  Total TP : {len(tps)}  →  showing {len(tp_sample)}")
# #     print(f"  Total FP : {len(fps)}  →  showing {len(fp_sample)}")
# #     print(f"  Writing  → {OUTPUT_FILE}")

# #     # ── WRITE ─────────────────────────────────────────────────────────────────

# #     lines = []

# #     def w(text=""):
# #         lines.append(text)

# #     # Header
# #     w(section("JobRadar — TP / FP Analysis Dump", "═"))
# #     w(f"Threshold : {args.threshold}  |  LLM floor : >= {args.llm_floor}")
# #     w(f"TP shown  : {len(tp_sample)} of {len(tps)} total")
# #     w(f"FP shown  : {len(fp_sample)} of {len(fps)} total")
# #     w(f"Sorted by : {args.sort} descending")
# #     w()

# #     # CV Work Profile
# #     w(section("YOUR CV WORK PROFILE", "═"))
# #     if cv_profile:
# #         w(cv_profile)
# #     else:
# #         w("(cv_work_profile.txt not found)")
# #     w()
# #     w(divider("═"))
# #     w()

# #     def write_entry(i, entry, bucket):
# #         w(divider("─"))
# #         w(f"{bucket} #{i+1}  |  {entry['company'].upper()}  |  {entry['title']}")
# #         w(f"LLM Score : {entry['llm_score']}/10   Cosine : {entry['cosine']:.4f}   Seniority : {entry['seniority']}")
# #         w()

# #         w("  EXTRACTED WORK PROFILE:")
# #         if entry["work_profile"]:
# #             w(f"  {entry['work_profile']}")
# #         else:
# #             w("  (empty)")
# #         w()

# #         if entry["reason"]:
# #             w("  LLM REASON:")
# #             w(f"  {entry['reason']}")
# #             w()

# #         if entry["gaps"]:
# #             w("  LLM GAPS:")
# #             w(f"  {entry['gaps']}")
# #             w()

# #         if entry["skills"]:
# #             w("  MATCHING SKILLS:")
# #             w(f"  {', '.join(entry['skills'])}")
# #             w()

# #         w("  FULL JD TEXT:")
# #         if entry["jd_text"]:
# #             w(entry["jd_text"])
# #         else:
# #             w("  (no JD text available)")
# #         w()

# #     # TRUE POSITIVES
# #     w(section(f"TRUE POSITIVES  (LLM >= {args.llm_floor}  AND  cosine >= {args.threshold})  [{len(tp_sample)} shown]", "█"))
# #     w("These are jobs the embedding CORRECTLY passed and LLM confirmed as good.")
# #     w("Use these to understand what a correct match looks like.")
# #     w()
# #     for i, entry in enumerate(tp_sample):
# #         write_entry(i, entry, "TP")

# #     w()
# #     w(divider("═"))
# #     w()

# #     # FALSE POSITIVES
# #     w(section(f"FALSE POSITIVES  (LLM < {args.llm_floor}  AND  cosine >= {args.threshold})  [{len(fp_sample)} shown]", "▓"))
# #     w("These are jobs the embedding PASSED but LLM scored below the floor.")
# #     w("These are your precision problem. Understand WHY they passed.")
# #     w()
# #     for i, entry in enumerate(fp_sample):
# #         write_entry(i, entry, "FP")

# #     # Write file
# #     with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
# #         f.write("\n".join(lines))

# #     print(f"\n  ✅ Done → {OUTPUT_FILE}")
# #     print(f"  File size: {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")
# #     print(f"\n  Paste the contents to Claude for analysis.\n")


# # if __name__ == "__main__":
# #     main()