"""Cluster labeling helpers for canonical role clustering."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence

import structlog

from analytics.clustering.config import cluster_label_dominance_threshold
from analytics.clustering.text import normalize_clustering_text_fragment
from analytics.clustering.types import (
    ClusteredPosting,
    ClusteringResult,
    ClusterSummary,
    PostingClusterFeatures,
)
from common.llm_adapter import complete

log = structlog.get_logger()

_LABEL_AGENT_NAME = "analytics-clustering-labeler"
_LABEL_MAX_TOKENS = 48
_RESPONSIBILITY_SAMPLE_LIMIT = 6
_REPRESENTATIVE_TITLE_LIMIT = 6
_DESCRIPTION_SKILL_LIMIT = 3
_DESCRIPTION_TOOL_LIMIT = 2
_DISAMBIGUATION_QUALIFIER_LIMIT = 3

ClusterLabeler = Callable[[ClusterSummary, Sequence[PostingClusterFeatures]], str | None]


def _feature_map(features_rows: Sequence[PostingClusterFeatures]) -> dict[str, PostingClusterFeatures]:
    feature_map: dict[str, PostingClusterFeatures] = {}
    duplicate_count = 0
    for row in features_rows:
        if row.posting_id in feature_map:
            duplicate_count += 1
            continue
        feature_map[row.posting_id] = row
    if duplicate_count:
        log.warning("clustering_label_duplicate_feature_rows", duplicate_count=duplicate_count)
    return feature_map


def _cluster_feature_rows(
    cluster: ClusterSummary,
    feature_map: dict[str, PostingClusterFeatures],
) -> list[PostingClusterFeatures]:
    return [feature_map[posting_id] for posting_id in cluster.member_posting_ids if posting_id in feature_map]


def _most_common_title(feature_rows: Sequence[PostingClusterFeatures]) -> tuple[str | None, float]:
    if not feature_rows:
        return None, 0.0
    counts = Counter(feature_row.title for feature_row in feature_rows)
    title, count = counts.most_common(1)[0]
    return title, count / len(feature_rows)


def _fallback_label(cluster: ClusterSummary, feature_rows: Sequence[PostingClusterFeatures]) -> str:
    title, _dominance = _most_common_title(feature_rows)
    if title:
        return title
    if cluster.representative_titles:
        return cluster.representative_titles[0]
    return cluster.cluster_id


def _responsibility_samples(feature_rows: Sequence[PostingClusterFeatures]) -> list[str]:
    seen: set[str] = set()
    samples: list[str] = []
    for feature_row in feature_rows:
        for responsibility in feature_row.responsibilities:
            normalized = normalize_clustering_text_fragment(responsibility)
            if not normalized:
                continue
            folded = normalized.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            samples.append(normalized)
            if len(samples) >= _RESPONSIBILITY_SAMPLE_LIMIT:
                return samples
    return samples


def _representative_title_samples(
    cluster: ClusterSummary,
    feature_rows: Sequence[PostingClusterFeatures],
) -> list[str]:
    samples: list[str] = []
    seen: set[str] = set()

    for title in cluster.representative_titles:
        normalized = normalize_clustering_text_fragment(title)
        if not normalized:
            continue
        folded = normalized.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        samples.append(normalized)
        if len(samples) >= _REPRESENTATIVE_TITLE_LIMIT:
            return samples

    ordered_feature_rows = sorted(
        feature_rows,
        key=lambda feature_row: (feature_row.title.casefold(), feature_row.posting_id),
    )
    for feature_row in ordered_feature_rows:
        normalized = normalize_clustering_text_fragment(feature_row.title)
        if not normalized:
            continue
        folded = normalized.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        samples.append(normalized)
        if len(samples) >= _REPRESENTATIVE_TITLE_LIMIT:
            return samples

    return samples


def _cluster_label_prompt(cluster: ClusterSummary, feature_rows: Sequence[PostingClusterFeatures]) -> str:
    top_titles = _representative_title_samples(cluster, feature_rows)
    top_skills = [skill.skill_name for skill in cluster.top_skills[:5]]
    top_tools = [tool.tool_name for tool in cluster.top_tools[:5]]
    responsibilities = _responsibility_samples(feature_rows)

    prompt_lines = [
        "Generate a concise canonical job role label.",
        "Return only the role label with no explanation.",
        f"Representative titles: {', '.join(top_titles) or 'n/a'}",
        f"Top skills: {', '.join(top_skills) or 'n/a'}",
        f"Top tools: {', '.join(top_tools) or 'n/a'}",
        f"Responsibilities: {', '.join(responsibilities) or 'n/a'}",
    ]
    return "\n".join(prompt_lines)


def _normalize_label_output(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    first_line = value.strip().splitlines()[0].strip() if value.strip() else ""
    normalized = first_line.strip("\"' ")
    return normalized or None


def _cluster_description(cluster: ClusterSummary, label: str) -> str:
    skills = [skill.skill_name for skill in cluster.top_skills[:_DESCRIPTION_SKILL_LIMIT]]
    tools = [tool.tool_name for tool in cluster.top_tools[:_DESCRIPTION_TOOL_LIMIT]]
    titles = [
        title
        for title in cluster.representative_titles[:_REPRESENTATIVE_TITLE_LIMIT]
        if title.casefold() != label.casefold()
    ]

    fragments: list[str] = []
    if skills:
        fragments.append(f"Common skills include {', '.join(skills)}")
    if tools:
        fragments.append(f"Common tools include {', '.join(tools)}")
    if not fragments and titles:
        fragments.append(f"Representative titles include {', '.join(titles[:3])}")

    if not fragments:
        return label
    return f"{label}. {' '.join(fragments)}"


def _default_llm_labeler(cluster: ClusterSummary, feature_rows: Sequence[PostingClusterFeatures]) -> str | None:
    prompt = _cluster_label_prompt(cluster, feature_rows)
    try:
        result = complete(
            prompt,
            agent_name=_LABEL_AGENT_NAME,
            max_tokens=_LABEL_MAX_TOKENS,
        )
    except Exception as exc:
        log.warning(
            "clustering_label_llm_exception",
            cluster_id=cluster.cluster_id,
            error_type=type(exc).__name__,
        )
        return None

    if not result.get("success") or result.get("extraction_failed"):
        log.warning("clustering_label_llm_failed", cluster_id=cluster.cluster_id)
        return None

    return _normalize_label_output(result.get("content"))


def _unique_signals(
    cluster: ClusterSummary,
    siblings: Sequence[ClusterSummary],
) -> tuple[list[str], list[str]]:
    """Return skills and tools present in *cluster* but absent from all *siblings*."""
    sibling_skills: set[str] = set()
    sibling_tools: set[str] = set()
    for sib in siblings:
        sibling_skills.update(s.skill_name.casefold() for s in sib.top_skills)
        sibling_tools.update(t.tool_name.casefold() for t in sib.top_tools)

    unique_skills = [s.skill_name for s in cluster.top_skills if s.skill_name.casefold() not in sibling_skills]
    unique_tools = [t.tool_name for t in cluster.top_tools if t.tool_name.casefold() not in sibling_tools]
    return unique_skills, unique_tools


def _deterministic_qualifier(
    cluster: ClusterSummary,
    siblings: Sequence[ClusterSummary],
) -> str | None:
    """Build a parenthetical qualifier from unique skills/tools to break a label tie."""
    unique_skills, unique_tools = _unique_signals(cluster, siblings)
    tokens = (unique_tools + unique_skills)[:_DISAMBIGUATION_QUALIFIER_LIMIT]
    if not tokens:
        tokens = [s.skill_name for s in cluster.top_skills[:_DISAMBIGUATION_QUALIFIER_LIMIT]]
    return "/".join(tokens) if tokens else None


def _disambiguation_prompt(
    base_label: str,
    cluster: ClusterSummary,
    feature_rows: Sequence[PostingClusterFeatures],
    siblings: Sequence[ClusterSummary],
) -> str:
    """Build a prompt that asks the LLM to produce a unique variant of *base_label*."""
    titles = _representative_title_samples(cluster, feature_rows)
    responsibilities = _responsibility_samples(feature_rows)
    unique_skills, unique_tools = _unique_signals(cluster, siblings)

    lines = [
        f'Multiple distinct clusters share the label "{base_label}".',
        "Generate a more specific role label that distinguishes THIS cluster.",
        "Use a parenthetical qualifier or an adjective — e.g. "
        '"Senior Data Engineer (Snowflake/dbt)" not just "Senior Data Engineer".',
        "Return only the role label with no explanation.",
        f"Representative titles: {', '.join(titles) or 'n/a'}",
        f"All skills: {', '.join(s.skill_name for s in cluster.top_skills[:7]) or 'n/a'}",
        f"All tools: {', '.join(t.tool_name for t in cluster.top_tools[:5]) or 'n/a'}",
    ]
    if unique_skills:
        lines.append(f"Unique skills (absent from sibling clusters): {', '.join(unique_skills[:5])}")
    if unique_tools:
        lines.append(f"Unique tools (absent from sibling clusters): {', '.join(unique_tools[:5])}")
    if responsibilities:
        lines.append(f"Responsibilities: {', '.join(responsibilities)}")
    return "\n".join(lines)


def _disambiguate_collisions(
    clusters: list[ClusterSummary],
    feature_map: dict[str, PostingClusterFeatures],
    *,
    allow_llm: bool,
) -> tuple[list[ClusterSummary], int]:
    """Detect duplicate labels and re-label colliding clusters.

    Returns the (possibly updated) cluster list and the count of labels that
    were successfully disambiguated.
    """
    label_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, cluster in enumerate(clusters):
        if cluster.label:
            label_to_indices[cluster.label.casefold()].append(idx)

    collisions = {lbl: idxs for lbl, idxs in label_to_indices.items() if len(idxs) > 1}
    if not collisions:
        return clusters, 0

    updated = list(clusters)
    disambiguated_count = 0

    for _label_key, indices in collisions.items():
        collision_clusters = [clusters[i] for i in indices]
        base_label = clusters[indices[0]].label or ""

        for pos, idx in enumerate(indices):
            cluster = clusters[idx]
            siblings = [c for j, c in enumerate(collision_clusters) if j != pos]
            cluster_features = _cluster_feature_rows(cluster, feature_map)

            new_label: str | None = None

            if allow_llm:
                prompt = _disambiguation_prompt(base_label, cluster, cluster_features, siblings)
                try:
                    result = complete(prompt, agent_name=_LABEL_AGENT_NAME, max_tokens=_LABEL_MAX_TOKENS)
                    if result.get("success") and not result.get("extraction_failed"):
                        candidate = _normalize_label_output(result.get("content"))
                        if candidate and candidate.casefold() != base_label.casefold():
                            new_label = candidate
                except Exception as exc:
                    log.warning(
                        "clustering_disambiguation_llm_exception",
                        cluster_id=cluster.cluster_id,
                        error_type=type(exc).__name__,
                    )

            if new_label is None:
                qualifier = _deterministic_qualifier(cluster, siblings)
                if qualifier:
                    new_label = f"{base_label} ({qualifier})"

            if new_label and new_label.casefold() != base_label.casefold():
                updated[idx] = cluster.model_copy(
                    update={
                        "label": new_label,
                        "description": _cluster_description(cluster, new_label),
                    }
                )
                disambiguated_count += 1

    log.info(
        "clustering_label_disambiguation_complete",
        collision_groups=len(collisions),
        disambiguated_count=disambiguated_count,
    )
    return updated, disambiguated_count


def label_clusters(
    result: ClusteringResult,
    features_rows: Sequence[PostingClusterFeatures],
    *,
    llm_labeler: ClusterLabeler | None = None,
    allow_llm_fallback: bool = True,
    dominance_threshold: float | None = None,
) -> ClusteringResult:
    """Apply dominant-title or LLM-generated labels to cluster summaries."""
    if result.skipped or not result.clusters:
        return result

    effective_dominance_threshold = (
        cluster_label_dominance_threshold() if dominance_threshold is None else dominance_threshold
    )
    if not 0.0 <= effective_dominance_threshold <= 1.0:
        raise ValueError("dominance_threshold must be between 0.0 and 1.0")

    feature_map = _feature_map(features_rows)
    effective_llm_labeler = llm_labeler or _default_llm_labeler

    updated_clusters: list[ClusterSummary] = []
    dominant_title_count = 0
    llm_generated_count = 0
    fallback_count = 0

    for cluster in result.clusters:
        cluster_features = _cluster_feature_rows(cluster, feature_map)
        dominant_title, dominant_share = _most_common_title(cluster_features)

        label = None
        label_source = cluster.label_source
        is_llm_generated_label = False

        if dominant_title and dominant_share >= effective_dominance_threshold:
            label = dominant_title
            label_source = "dominant_title"
            dominant_title_count += 1
        elif allow_llm_fallback:
            llm_label = effective_llm_labeler(cluster, cluster_features)
            if llm_label:
                label = llm_label
                label_source = "llm"
                is_llm_generated_label = True
                llm_generated_count += 1

        if label is None:
            label = _fallback_label(cluster, cluster_features)
            label_source = "fallback"
            fallback_count += 1

        updated_cluster = cluster.model_copy(
            update={
                "label": label,
                "label_source": label_source,
                "description": _cluster_description(cluster, label),
                "is_llm_generated_label": is_llm_generated_label,
            }
        )
        updated_clusters.append(updated_cluster)

    updated_clusters, disambiguated_count = _disambiguate_collisions(
        updated_clusters,
        feature_map,
        allow_llm=allow_llm_fallback,
    )

    label_by_cluster_id: dict[str, str] = {}
    for cluster in updated_clusters:
        label_by_cluster_id[cluster.cluster_id] = cluster.label or cluster.cluster_id

    updated_assignments: list[ClusteredPosting] = []
    for assignment in result.assignments:
        cluster_label = None
        if assignment.cluster_id is not None:
            cluster_label = label_by_cluster_id.get(assignment.cluster_id)
        updated_assignments.append(
            assignment.model_copy(
                update={
                    "cluster_label": cluster_label,
                }
            )
        )

    log.info(
        "clustering_labels_applied",
        cluster_count=len(updated_clusters),
        dominant_title_count=dominant_title_count,
        llm_generated_count=llm_generated_count,
        fallback_count=fallback_count,
        disambiguated_count=disambiguated_count,
        dominance_threshold=effective_dominance_threshold,
    )

    return result.model_copy(
        update={
            "clusters": updated_clusters,
            "assignments": updated_assignments,
        }
    )
