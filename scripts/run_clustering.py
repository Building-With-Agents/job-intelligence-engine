# ruff: noqa: T201
"""Run canonical role clustering on live DB data and print findings.

Usage:
    python scripts/run_clustering.py
    python scripts/run_clustering.py --min-postings 100

Phase 2 tuning (JIE #327) — one variable per run discipline:
    python scripts/run_clustering.py --dry-run --findings-output eval/runs/findingsv2.X-clustering-tierN.md
    # Set a single param via env override, e.g.:
    CLUSTER_MIN_SAMPLES=2 python scripts/run_clustering.py --dry-run --findings-output eval/runs/...
    CLUSTER_DIM_REDUCTION_N_COMPONENTS=10 python scripts/run_clustering.py --dry-run ...

Tuning matrix (execute in priority order per #327 Phase 2 discipline):
    Priority 1: CLUSTER_MIN_SAMPLES 3 → try 2, 5
    Priority 2: CLUSTER_MIN_CLUSTER_SIZE 3 → try 2, 5
    Priority 3: CLUSTER_DIM_REDUCTION_N_COMPONENTS 15 → try 10, 20
    Priority 4: CLUSTER_UMAP_N_NEIGHBORS 10 → try 5, 15
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.env import load_repo_root_dotenv

load_repo_root_dotenv()

from analytics.canonical_roles.loader import load_posting_cluster_features
from analytics.canonical_roles.persist import (
    cleanup_orphan_canonical_roles,
    persist_clustering_result,
)
from analytics.canonical_roles.snapshots import refresh_role_snapshot_weekly
from analytics.clustering.config import cluster_min_total_postings
from analytics.clustering.embeddings import embed_posting_features
from analytics.clustering.pipeline import run_clustering_pipeline
from common.data_store.database import check_db_connection, session_scope


def _iso_week_monday(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _write_findings_md(path: Path, findings: dict, config_snapshot: dict) -> None:
    """Write a Phase 2 tuning findings note in Markdown (JIE #327)."""
    mega = findings["mega_cluster_share_pct"]
    noise = findings["noise_rate"]
    n_clusters = findings["clusters_found"]
    gate = (
        "PASS"
        if n_clusters >= 20
        and mega < 10.0
        and findings["noise_count"] / max(findings["eligible_posting_count"], 1) < 0.30
        else "FAIL"
    )
    lines = [
        f"# Phase 2 Clustering Findings — {findings['run_date']}",
        "",
        f"**Gate:** {gate}  (target: clusters ≥ 20, mega-cluster < 10%, noise < 30%)",
        "",
        "## Run parameters",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
    ]
    for k, v in config_snapshot.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines += [
        "",
        "## Results",
        "",
        "| Metric | Value | Target |",
        "|--------|-------|--------|",
        f"| eligible_posting_count | {findings['eligible_posting_count']} | — |",
        f"| cluster_count | {n_clusters} | ≥ 20 |",
        f"| noise_rate | {noise} | < 30% |",
        f"| mega_cluster_share | {mega:.1f}% | < 10% |",
        f"| emergence_candidates | {findings['emergence_candidates']} | — |",
        "",
        "## Top clusters (qualitative label review)",
        "",
        "| # | Label | Posts | Top skills |",
        "|---|-------|-------|-----------|",
    ]
    for i, cl in enumerate(findings.get("top_clusters", [])[:20], 1):
        skills = ", ".join(cl["top_skills"][:4])
        lines.append(f"| {i} | {cl['label']} | {cl['posting_count']} | {skills} |")
    lines += [
        "",
        "## Decision",
        "",
        "<!-- Fill in after reviewing qualitative labels -->",
        "",
        f"- [ ] cluster_count ≥ 20 → {'✓' if n_clusters >= 20 else '✗'}",
        f"- [ ] mega_cluster_share < 10% → {'✓' if mega < 10.0 else '✗'}",
        f"- [ ] noise < 30% → {'✓' if findings['noise_count'] / max(findings['eligible_posting_count'], 1) < 0.30 else '✗'}",
        "",
        "**Next step:** <!-- advance to next Phase 2 lever / ship / revisit UMAP params -->",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nFindings Markdown written to: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-postings", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip DB persistence (clustering + findings only; no canonical_roles or snapshots updated)",
    )
    parser.add_argument(
        "--findings-output",
        type=str,
        default=None,
        metavar="PATH",
        help="Write Phase 2 findings Markdown to this path (e.g. eval/runs/findingsv2.1-clustering-tier3.md)",
    )
    args = parser.parse_args()

    if args.min_postings is not None:
        os.environ["CLUSTER_MIN_TOTAL_POSTINGS"] = str(args.min_postings)

    dry_run: bool = args.dry_run
    findings_output: str | None = args.findings_output

    if not check_db_connection():
        print("ERROR: DB not reachable")
        sys.exit(1)

    print("=" * 70)
    print("CANONICAL ROLE CLUSTERING — LIVE DATA RUN")
    print("=" * 70)

    with session_scope() as session:
        features = load_posting_cluster_features(session)
        min_threshold = cluster_min_total_postings()
        print(f"\nFeatures loaded:  {len(features)}")
        print(f"Min threshold:    {min_threshold}")

        if len(features) < min_threshold:
            print(f"SKIP: {len(features)} < {min_threshold}")
            return

        with_skills = sum(1 for f in features if f.skills)
        with_tools = sum(1 for f in features if f.tools)
        with_resp = sum(1 for f in features if f.responsibilities)
        print(f"With skills:      {with_skills} ({100 * with_skills / len(features):.1f}%)")
        print(f"With tools:       {with_tools} ({100 * with_tools / len(features):.1f}%)")
        print(f"With respons.:    {with_resp} ({100 * with_resp / len(features):.1f}%)")

        print("\n--- Generating embeddings (Azure OpenAI) ---")
        embedded = embed_posting_features(features, allow_partial=False)
        if embedded is None:
            print("ERROR: Embedding generation failed")
            sys.exit(1)
        print(f"Embeddings count: {len(embedded)}")

        print("\n--- Running HDBSCAN clustering ---")
        result = run_clustering_pipeline(features, embedded)

        if result.skipped:
            print(f"SKIP: {result.skip_reason}")
            return

        n_clusters = len(result.clusters)
        noise_rate = 100 * result.noise_posting_count / result.eligible_posting_count
        sorted_clusters = sorted(result.clusters, key=lambda c: c.member_count, reverse=True)
        mega_cluster_count = sorted_clusters[0].member_count if sorted_clusters else 0
        mega_cluster_share = (
            100 * mega_cluster_count / result.eligible_posting_count if result.eligible_posting_count else 0
        )

        print("\nClustering results:")
        print(f"  Total input:        {result.total_input_postings}")
        print(f"  Eligible:           {result.eligible_posting_count}")
        print(f"  Clusters found:     {n_clusters}  (target: ≥ 20)")
        print(f"  Noise postings:     {result.noise_posting_count}")
        print(f"  Noise rate:         {noise_rate:.1f}%  (target: < 30%)")
        print(f"  Mega-cluster share: {mega_cluster_share:.1f}%  (target: < 10%)")
        print(f"  Emergence cands:    {len(result.emergence_candidates)}")
        gate_pass = n_clusters >= 20 and mega_cluster_share < 10.0 and noise_rate < 30.0
        print(f"\n  Phase 2 gate: {'PASS' if gate_pass else 'FAIL'}  (clusters≥20, mega<10%, noise<30%)")

        print("\n--- Top clusters ---")
        for i, cl in enumerate(sorted_clusters[:20], 1):
            skills_str = ", ".join(s.skill_name for s in cl.top_skills[:5])
            tools_str = ", ".join(t.tool_name for t in cl.top_tools[:3])
            print(f"  {i:>2}. [{cl.member_count:>3} posts] {cl.label}")
            print(f"      Skills: {skills_str}")
            if tools_str:
                print(f"      Tools:  {tools_str}")
            print(f"      Titles: {', '.join(cl.representative_titles[:3])}")

        if result.emergence_candidates:
            print("\n--- Emergence candidates ---")
            for ec in result.emergence_candidates:
                print(f"  - {ec.candidate_role_label} ({ec.posting_count} posts)")
                print(f"    Novel skills: {[s.skill_name for s in ec.top_skills[:5]]}")
                print(f"    Reason: {ec.filter_reason}")

        if dry_run:
            print("\n--- dry-run: skipping DB persistence ---")
            persist_info: dict = {"roles_inserted": 0, "postings_updated": 0}
            snapshot_rows = 0
        else:
            print("\n--- Persisting to DB ---")
            persist_info = persist_clustering_result(session, result, correlation_id="week7-clustering-run")
            print(f"  Roles inserted:    {persist_info.get('roles_inserted')}")
            print(f"  Postings updated:  {persist_info.get('postings_updated')}")

            week_start = _iso_week_monday(date.today())
            print(f"\n--- Role snapshot weekly (week_start={week_start}) ---")
            snapshot_rows = refresh_role_snapshot_weekly(session, week_start=week_start)
            print(f"  Snapshot rows:     {snapshot_rows}")

            orphans = cleanup_orphan_canonical_roles(session)
            print(f"  Orphans cleaned:   {orphans}")

        print("\n" + "=" * 70)
        print("DONE — query canonical_roles and role_snapshot_weekly for findings")
        print("=" * 70)

        from analytics.clustering.config import (
            cluster_dim_reduction_n_components,
            cluster_min_cluster_size,
            cluster_min_samples,
            cluster_selection_method,
            cluster_umap_metric,
            cluster_umap_min_dist,
            cluster_umap_n_neighbors,
        )

        config_snapshot = {
            "min_cluster_size": cluster_min_cluster_size(),
            "min_samples": cluster_min_samples(),
            "cluster_selection_method": cluster_selection_method(),
            "umap_n_components": cluster_dim_reduction_n_components(),
            "umap_n_neighbors": cluster_umap_n_neighbors(),
            "umap_min_dist": cluster_umap_min_dist(),
            "umap_metric": cluster_umap_metric(),
        }

        findings = {
            "run_date": date.today().isoformat(),
            "config_snapshot": config_snapshot,
            "features_loaded": len(features),
            "eligible_posting_count": result.eligible_posting_count,
            "skills_coverage": f"{100 * with_skills / len(features):.1f}%",
            "tools_coverage": f"{100 * with_tools / len(features):.1f}%",
            "clusters_found": n_clusters,
            "noise_count": result.noise_posting_count,
            "noise_rate": f"{noise_rate:.1f}%",
            "mega_cluster_share_pct": mega_cluster_share,
            "phase2_gate_pass": gate_pass,
            "emergence_candidates": len(result.emergence_candidates),
            "roles_persisted": persist_info.get("roles_inserted"),
            "snapshot_rows": snapshot_rows,
            "top_clusters": [
                {
                    "label": cl.label,
                    "posting_count": cl.member_count,
                    "top_skills": [s.skill_name for s in cl.top_skills[:5]],
                    "top_tools": [t.tool_name for t in cl.top_tools[:3]],
                    "representative_titles": cl.representative_titles[:3],
                }
                for cl in sorted_clusters[:20]
            ],
            "emergence_details": [
                {
                    "label": ec.candidate_role_label,
                    "count": ec.posting_count,
                    "novel_skills": [s.skill_name for s in ec.top_skills[:5]],
                    "reason": ec.filter_reason,
                }
                for ec in result.emergence_candidates
            ],
        }

        out_path = Path(__file__).parent.parent / "data" / "analytics" / "clustering_findings.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")
        print(f"\nFindings JSON saved to: {out_path}")

        if findings_output:
            _write_findings_md(Path(findings_output), findings, config_snapshot)


if __name__ == "__main__":
    main()
