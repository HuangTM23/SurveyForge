import argparse
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from ..core.io import ROOT, dump_json, load_json, read_jsonl, stage_temp, write_jsonl, write_text
from ..core.llm_client import LLMClient, filter_available_providers
from ..core.text import normalize_space, slugify


def log(message: str) -> None:
    print(message, flush=True)


def batch(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def parse_provider_list(provider: str, providers: str) -> List[str]:
    items = []
    if providers:
        items.extend([normalize_space(item) for item in providers.split(",") if normalize_space(item)])
    primary = normalize_space(provider)
    if primary:
        items = [item for item in items if item != primary]
        items.insert(0, primary)
    if not items:
        items.append("auto")
    return items


def compact_filter_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "paper_id": row.get("paper_id", ""),
        "title": row.get("title", ""),
        "venue": row.get("venue", ""),
        "publisher": row.get("publisher", ""),
        "citation_count": row.get("citation_count", 0),
        "year": row.get("year", ""),
    }


def render_md(title: str, rows: List[Dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('title', '')}",
                f"- Paper ID: {row.get('paper_id', '')}",
                f"- Category: {row.get('category_id', '') or 'unknown'}",
                f"- Venue: {row.get('venue', '') or 'unknown'}",
                f"- Year: {row.get('year', '') or 'unknown'}",
                f"- Citation Count: {row.get('citation_count', 0)}",
                f"- DOI: {row.get('doi', '') or 'unknown'}",
                f"- URL: {row.get('url', '') or 'unknown'}",
                f"- Reason: {row.get('selection_reason') or row.get('filter_reason') or ''}",
                f"- Abstract: {row.get('abstract', '') or 'unknown'}",
                "",
            ]
        )
    return "\n".join(lines)


def generate_json_with_fallback(provider_pool: List[str], prompt_path: Path, payload: Dict[str, Any], label: str) -> Dict[str, Any]:
    last_error = None
    for provider in provider_pool:
        try:
            client = LLMClient(provider=provider)
            return client.generate_json(prompt_path, payload)
        except Exception as exc:
            last_error = exc
            log(f"{label}: provider={provider} failed, trying fallback if available: {exc}")
    raise RuntimeError(f"{label}: all providers failed: {last_error}")


def llm_filter(provider_pool: List[str], planning: Dict[str, Any], rows: List[Dict[str, Any]], batch_size: int) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    parts = batch(rows, batch_size)
    log(f"[Step3.1] LLM candidate filtering: {len(rows)} papers, {len(parts)} batches, batch_size={batch_size}")
    for idx, part in enumerate(parts, 1):
        start = (idx - 1) * batch_size + 1
        end = start + len(part) - 1
        log(f"[Step3.1] Filtering batch {idx}/{len(parts)} papers {start}-{end} ...")
        compact_part = [compact_filter_candidate(row) for row in part]
        result = generate_json_with_fallback(
            provider_pool,
            ROOT / "prompts/candidate_filter.md",
            {"planning": planning, "candidates": compact_part},
            f"[Step3.1] Batch {idx}/{len(parts)}",
        )
        decisions = {d.get("paper_id"): d for d in result.get("decisions", []) if isinstance(d, dict)}
        batch_kept = 0
        for row in part:
            decision = decisions.get(row.get("paper_id"), {})
            merged = dict(row)
            merged.update(
                {
                    "filter_keep": bool(decision.get("keep")),
                    "filter_reason": normalize_space(decision.get("reason", "")),
                    "filter_scores": {
                        "relevance": decision.get("relevance_score"),
                        "quality": decision.get("quality_score"),
                        "method": decision.get("method_score"),
                    },
                }
            )
            if merged["filter_keep"]:
                batch_kept += 1
                kept.append(merged)
        log(f"[Step3.1] Batch {idx}/{len(parts)} kept {batch_kept}/{len(part)}; total kept={len(kept)}")
    return kept


def classify_titles(provider_pool: List[str], planning: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    titles = [{"paper_id": row.get("paper_id"), "title": row.get("title")} for row in rows]
    prompt = planning.get("title_classification_system_prompt") or (ROOT / "prompts/title_classify.md").read_text(encoding="utf-8")
    log(f"[Step3.4] Title classification: sending {len(titles)} titles to LLM")
    return generate_json_with_fallback(
        provider_pool,
        ROOT / "prompts/title_classify.md",
        {"system_prompt_hint": prompt, "titles": titles},
        "[Step3.4] Title classification",
    )


def assign_clusters(rows: List[Dict[str, Any]], clusters: Dict[str, Any], out_dir: Path) -> List[Dict[str, Any]]:
    by_id = {row.get("paper_id"): dict(row) for row in rows}
    assigned: List[Dict[str, Any]] = []
    log(f"[Step3.4] Assigning clusters: {len(clusters.get('clusters', []))} categories")
    for cluster in clusters.get("clusters", []):
        category_id = slugify(cluster.get("category_id") or cluster.get("category_name", "category")).replace("-", "_")
        category_name = normalize_space(cluster.get("category_name", category_id))
        category_rows = []
        for paper_id in cluster.get("paper_ids", []):
            if paper_id not in by_id:
                continue
            row = dict(by_id[paper_id])
            row["category_id"] = category_id
            row["category_name"] = category_name
            row["category_rationale"] = normalize_space(cluster.get("rationale", ""))
            assigned.append(row)
            category_rows.append(row)
        if category_rows:
            write_jsonl(out_dir / "categories" / f"{category_id}.jsonl", category_rows)
            write_text(out_dir / "categories" / f"{category_id}.md", render_md(category_name, category_rows))
            log(f"[Step3.4]   {category_id}: {len(category_rows)} papers")
    return assigned


def compact_intent_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "paper_id": row.get("paper_id", ""),
        "title": row.get("title", ""),
        "abstract": row.get("abstract", ""),
        "venue": row.get("venue", ""),
        "publisher": row.get("publisher", ""),
        "year": row.get("year", ""),
        "publication_type": row.get("publication_type", ""),
        "citation_count": row.get("citation_count", 0),
        "doi": row.get("doi", ""),
    }


def select_category_with_provider(
    provider: str,
    planning: Dict[str, Any],
    category_id: str,
    category_rows: List[Dict[str, Any]],
    category_target: int,
) -> List[Dict[str, Any]]:
    client = LLMClient(provider=provider or "auto")
    result = client.generate_json(
        ROOT / "prompts/intent_select.md",
        {
            "planning": planning,
            "category_id": category_id,
            "target_count_for_this_category": category_target,
            "papers": [compact_intent_candidate(row) for row in category_rows],
        },
    )
    selected = []
    for decision in result.get("selected", []) if isinstance(result, dict) else []:
        if isinstance(decision, dict) and decision.get("paper_id"):
            selected.append(decision)
    return selected


def select_by_category(planning: Dict[str, Any], rows: List[Dict[str, Any]], provider_pool: List[str]) -> List[Dict[str, Any]]:
    target = int(planning.get("target_keep_count", 30))
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("category_id") or "uncategorized", []).append(row)
    selected: List[Dict[str, Any]] = []
    total = max(1, len(rows))
    vote_records: List[Dict[str, Any]] = []
    log(
        f"[Step3.5] Multi-provider intent selection by category: "
        f"{len(grouped)} categories, target={target}, providers={', '.join(provider_pool)}"
    )
    for idx, (category_id, category_rows) in enumerate(grouped.items(), 1):
        category_target = max(1, math.ceil(target * len(category_rows) / total))
        log(
            f"[Step3.5] Selecting category {idx}/{len(grouped)} "
            f"{category_id}: {len(category_rows)} papers, target~{category_target}, providers={len(provider_pool)}"
        )
        votes: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"providers": [], "reasons": [], "scores": []})
        for provider in provider_pool:
            log(f"[Step3.5]   provider={provider} selecting {category_id} ...")
            try:
                provider_selected = select_category_with_provider(provider, planning, category_id, category_rows, category_target)
            except Exception as exc:
                log(f"[Step3.5]   provider={provider} failed for {category_id}: {exc}")
                continue
            log(f"[Step3.5]   provider={provider} selected {len(provider_selected)} papers")
            for decision in provider_selected:
                paper_id = decision.get("paper_id")
                vote = votes[paper_id]
                vote["providers"].append(provider)
                vote["reasons"].append(normalize_space(decision.get("reason", "")))
                scores = {
                    "relevance": decision.get("relevance_score"),
                    "novelty": decision.get("novelty_score"),
                    "methodology": decision.get("methodology_score"),
                    "venue": decision.get("venue_score"),
                    "citation": decision.get("citation_score"),
                }
                vote["scores"].append(scores)

        ranked_category = []
        by_id = {row.get("paper_id"): row for row in category_rows}
        for paper_id, vote in votes.items():
            row = by_id.get(paper_id)
            if not row:
                continue
            score_values = []
            for score in vote["scores"]:
                score_values.append(sum(float(value or 0) for value in score.values()))
            avg_score = sum(score_values) / len(score_values) if score_values else 0.0
            merged = dict(row)
            merged["selection_reason"] = "; ".join([reason for reason in vote["reasons"] if reason])
            merged["selection_scores"] = {
                "provider_vote_count": len(set(vote["providers"])),
                "provider_vote_ratio": len(set(vote["providers"])) / max(1, len(provider_pool)),
                "average_total_score": avg_score,
            }
            merged["selection_providers"] = sorted(set(vote["providers"]))
            ranked_category.append(merged)
            vote_records.append(
                {
                    "category_id": category_id,
                    "paper_id": paper_id,
                    "providers": sorted(set(vote["providers"])),
                    "vote_count": len(set(vote["providers"])),
                    "vote_ratio": len(set(vote["providers"])) / max(1, len(provider_pool)),
                    "average_total_score": avg_score,
                    "reasons": vote["reasons"],
                }
            )
        ranked_category.sort(
            key=lambda row: (
                row.get("selection_scores", {}).get("provider_vote_count", 0),
                row.get("selection_scores", {}).get("average_total_score", 0),
                int(row.get("citation_count") or 0),
            ),
            reverse=True,
        )
        category_selected = 0
        for merged in ranked_category[:category_target]:
            selected.append(merged)
            category_selected += 1
        log(f"[Step3.5]   consensus selected {category_selected}/{len(category_rows)} from {category_id}")
    selected.sort(
        key=lambda row: (
            row.get("selection_scores", {}).get("provider_vote_count", 0),
            row.get("selection_scores", {}).get("average_total_score", 0),
            int(row.get("citation_count") or 0),
        ),
        reverse=True,
    )
    select_by_category.vote_records = vote_records  # type: ignore[attr-defined]
    return selected[:target]


def main():
    parser = argparse.ArgumentParser(description="Stage1 Step3: LLM filter, classify, and multi-provider select papers.")
    parser.add_argument("--planning", default=str(ROOT / "temp/stage1/step1/planning.json"))
    parser.add_argument("--candidates", default=str(ROOT / "temp/stage1/step2/candidate_pool_pre_filtered.jsonl"))
    parser.add_argument("--provider", default="")
    parser.add_argument("--providers", default="")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()

    planning = load_json(Path(args.planning))
    rows = read_jsonl(Path(args.candidates))
    out_dir = stage_temp("stage1", "step3")
    log("=" * 72)
    log("Stage1 Step3: LLM screening / classification / selection")
    log("=" * 72)
    log(f"[Input] Planning: {args.planning}")
    log(f"[Input] Candidates: {args.candidates}")
    log(f"[Input] Candidate count: {len(rows)}")
    requested_provider_pool = parse_provider_list(args.provider, args.providers)
    log(f"[Provider] Requested provider pool: {', '.join(requested_provider_pool)}")
    provider_pool = filter_available_providers(requested_provider_pool, log)
    primary_provider = provider_pool[0]
    log(f"[Input] Provider pool: {', '.join(provider_pool)}")
    log(f"[Input] Primary provider for candidate filter/title classification: {primary_provider}")
    log(f"[Input] Batch size: {args.batch_size}")
    log(f"[Output] Directory: {out_dir}")
    filtered = llm_filter(provider_pool, planning, rows, args.batch_size)
    write_jsonl(out_dir / "candidate_pool_filtered.jsonl", filtered)
    write_text(out_dir / "candidate_pool_filtered.md", render_md("Candidate Pool Filtered", filtered))
    log(f"[Output] Filtered pool: {out_dir / 'candidate_pool_filtered.jsonl'} ({len(filtered)} papers)")

    log("[Step3.2] Abstract completion removed; using metadata enriched in Stage1 Step2.")
    clusters = classify_titles(provider_pool, planning, filtered)
    dump_json(out_dir / "title_clusters.json", clusters)
    log(f"[Output] Title clusters: {out_dir / 'title_clusters.json'}")
    clustered = assign_clusters(filtered, clusters, out_dir)
    write_jsonl(out_dir / "candidate_pool_clustered.jsonl", clustered)
    log(f"[Output] Clustered pool: {out_dir / 'candidate_pool_clustered.jsonl'} ({len(clustered)} papers)")

    selected = select_by_category(planning, clustered, provider_pool)
    vote_records = getattr(select_by_category, "vote_records", [])
    write_jsonl(out_dir / "selection_votes.jsonl", vote_records)
    log(f"[Output] Selection votes: {out_dir / 'selection_votes.jsonl'} ({len(vote_records)} records)")
    write_jsonl(out_dir / "selected_papers.jsonl", selected)
    write_text(out_dir / "selected_papers.md", render_md("Selected Papers", selected))
    log(f"[Output] Selected papers: {out_dir / 'selected_papers.jsonl'} ({len(selected)} papers)")
    log("Stage1 Step3 finished.")


if __name__ == "__main__":
    main()
