"""One-shot script to pull Langfuse generation data and compute cost projections."""
import os, sys, requests, json
from datetime import datetime
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

base = os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000")
pk = os.getenv("LANGFUSE_PUBLIC_KEY")
sk = os.getenv("LANGFUSE_SECRET_KEY")

# Paginate generations (cap at 3000)
all_gens = []
page = 1
while len(all_gens) < 3000:
    resp = requests.get(f"{base}/api/public/observations?type=GENERATION&limit=100&page={page}", auth=(pk, sk))
    data = resp.json()
    batch = data.get("data", [])
    if not batch:
        break
    all_gens.extend(batch)
    page += 1
    total = data.get("meta", {}).get("totalItems", 0)
    if len(all_gens) >= total:
        break

print(f"Total generations in Langfuse: {data.get('meta', {}).get('totalItems', 0)}")
print(f"Sampled: {len(all_gens)}")

# Categorise
cat_data = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "count": 0, "latencies": []})

for g in all_gens:
    usage = g.get("usage") or {}
    inp = usage.get("input", 0) or 0
    out = usage.get("output", 0) or 0

    name = (g.get("name", "") or "").lower()
    if "responsibilit" in name:
        cat = "extract_responsibilities"
    elif "skill" in name:
        cat = "extract_skills"
    elif "soc" in name:
        cat = "enrich_soc"
    elif "spam" in name:
        cat = "enrich_spam"
    elif "naics" in name:
        cat = "enrich_naics"
    elif "employer" in name:
        cat = "enrich_employer"
    elif "task" in name and "enrich" not in name and "analytics" not in name:
        cat = "extract_tasks"
    else:
        cat = "other"

    if inp > 0 or out > 0:
        cat_data[cat]["input_tokens"] += inp
        cat_data[cat]["output_tokens"] += out
        cat_data[cat]["count"] += 1

    s, e = g.get("startTime"), g.get("endTime")
    if s and e:
        try:
            st = datetime.fromisoformat(s.replace("Z", "+00:00"))
            et = datetime.fromisoformat(e.replace("Z", "+00:00"))
            lat = (et - st).total_seconds()
            if 0 < lat < 300:
                cat_data[cat]["latencies"].append(lat)
        except Exception:
            pass

# ---------- Pricing per 1M tokens ----------
PRICING = {
    "azure":    {"input": 0.40, "output": 1.60},
    "gemini":   {"input": 0.15, "output": 0.60},
    "deepseek": {"input": 0.28, "output": 0.42},
    "groq":     {"input": 0.00, "output": 0.00},
}

def cost_usd(in_tok, out_tok, provider):
    p = PRICING[provider]
    return (in_tok / 1_000_000 * p["input"]) + (out_tok / 1_000_000 * p["output"])

# ---------- Print per-call averages ----------
print(f"\n{'Category':<25} {'Calls':>6} {'Avg In':>8} {'Avg Out':>8} {'Avg Tot':>10} {'Avg Lat':>10}")
print("-" * 75)
for cat in ["extract_tasks", "extract_responsibilities", "extract_skills",
            "enrich_soc", "enrich_naics", "enrich_employer", "other"]:
    d = cat_data[cat]
    if d["count"] == 0:
        continue
    avg_in = d["input_tokens"] / d["count"]
    avg_out = d["output_tokens"] / d["count"]
    avg_lat = sum(d["latencies"]) / len(d["latencies"]) if d["latencies"] else 0
    print(f"{cat:<25} {d['count']:>6} {avg_in:>8.0f} {avg_out:>8.0f} {avg_in+avg_out:>10.0f} {avg_lat:>9.1f}s")

# ---------- Per-job estimates ----------
extract_cats = ["extract_tasks", "extract_responsibilities", "extract_skills", "other"]
enrich_cats  = ["enrich_soc", "enrich_naics", "enrich_employer"]

ext_in  = sum(cat_data[c]["input_tokens"]  for c in extract_cats)
ext_out = sum(cat_data[c]["output_tokens"] for c in extract_cats)
ext_cnt = sum(cat_data[c]["count"]         for c in extract_cats)
enr_in  = sum(cat_data[c]["input_tokens"]  for c in enrich_cats)
enr_out = sum(cat_data[c]["output_tokens"] for c in enrich_cats)
enr_cnt = sum(cat_data[c]["count"]         for c in enrich_cats)

est_jobs = max(ext_cnt / 3, enr_cnt / 3) if ext_cnt > 0 else 1

ext_in_pj  = ext_in  / est_jobs
ext_out_pj = ext_out / est_jobs
enr_in_pj  = enr_in  / est_jobs
enr_out_pj = enr_out / est_jobs
tot_in_pj  = ext_in_pj + enr_in_pj
tot_out_pj = ext_out_pj + enr_out_pj

print(f"\n=== PER-JOB TOKEN ESTIMATES ({est_jobs:.0f} jobs sampled) ===")
print(f"Extraction: {ext_in_pj:.0f} in + {ext_out_pj:.0f} out = {ext_in_pj+ext_out_pj:.0f} tokens")
print(f"Enrichment: {enr_in_pj:.0f} in + {enr_out_pj:.0f} out = {enr_in_pj+enr_out_pj:.0f} tokens")
print(f"Total/job:  {tot_in_pj:.0f} in + {tot_out_pj:.0f} out = {tot_in_pj+tot_out_pj:.0f} tokens")

N = 1000
print(f"\n=== COST PER {N:,} JOBS BY TIER ===")

prem_ext = cost_usd(ext_in_pj*N, ext_out_pj*N, "azure")
prem_enr = cost_usd(enr_in_pj*N, enr_out_pj*N, "azure")
print(f"Premium (all Azure gpt-4.1-mini):  extract ${prem_ext:.2f} + enrich ${prem_enr:.2f} = ${prem_ext+prem_enr:.2f}")

mid_ext = cost_usd(ext_in_pj*N, ext_out_pj*N, "azure")
mid_enr = cost_usd(enr_in_pj*N, enr_out_pj*N, "gemini")
print(f"Mid-Tier (Azure + Gemini):         extract ${mid_ext:.2f} + enrich ${mid_enr:.2f} = ${mid_ext+mid_enr:.2f}")

bud_ext = cost_usd(ext_in_pj*N, ext_out_pj*N, "deepseek")
bud_enr = cost_usd(enr_in_pj*N, enr_out_pj*N, "groq")
print(f"Budget (DeepSeek + Groq free):     extract ${bud_ext:.2f} + enrich ${bud_enr:.2f} = ${bud_ext+bud_enr:.2f}")

print(f"\n=== MONTHLY PROJECTION (1,000 jobs/day x 30 days) ===")
print(f"Premium: ${(prem_ext+prem_enr)*30:.2f}/month")
print(f"Mid-Tier: ${(mid_ext+mid_enr)*30:.2f}/month")
print(f"Budget: ${(bud_ext+bud_enr)*30:.2f}/month")

# JSON export for the executive summary
report = {
    "total_generations": data.get("meta", {}).get("totalItems", 0),
    "sampled": len(all_gens),
    "estimated_jobs": int(est_jobs),
    "per_job_tokens": {"input": int(tot_in_pj), "output": int(tot_out_pj), "total": int(tot_in_pj+tot_out_pj)},
    "cost_per_1k_jobs": {
        "premium_azure": round(prem_ext+prem_enr, 2),
        "mid_azure_gemini": round(mid_ext+mid_enr, 2),
        "budget_deepseek_groq": round(bud_ext+bud_enr, 2),
    },
    "monthly_30k_jobs": {
        "premium": round((prem_ext+prem_enr)*30, 2),
        "mid": round((mid_ext+mid_enr)*30, 2),
        "budget": round((bud_ext+bud_enr)*30, 2),
    },
}
print(f"\n=== JSON EXPORT ===")
print(json.dumps(report, indent=2))
