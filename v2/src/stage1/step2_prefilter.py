import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.io import ROOT, dump_json, load_json, read_jsonl, stage_temp, write_jsonl, write_text
from ..core.text import normalize_space


def lower_terms(items: Any) -> List[str]:
    if not items:
        return []
    if not isinstance(items, list):
        items = [items]
    return [normalize_space(item).lower() for item in items if normalize_space(item)]


def contains_any(text: str, terms: List[str]) -> str:
    text_l = normalize_space(text).lower()
    for term in terms:
        if term and term in text_l:
            return term
    return ""


def filter_row(row: Dict[str, Any], planning: Dict[str, Any]) -> Tuple[bool, str]:
    retrieval = planning.get("retrieval", {}) or {}
    year_range = planning.get("year_range", {}) or {}
    title_terms = lower_terms(retrieval.get("title_exclude_terms"))
    venue_terms = lower_terms(retrieval.get("venue_block_terms"))

    year = row.get("year")
    if year:
        try:
            year_i = int(year)
            if year_i < int(year_range.get("from", 0)) or year_i > int(year_range.get("to", 9999)):
                return False, f"year_out_of_range:{year_i}"
        except (TypeError, ValueError):
            pass

    title_hit = contains_any(row.get("title", ""), title_terms)
    if title_hit:
        return False, f"title_exclude:{title_hit}"

    venue_text = " ".join(
        [
            str(row.get("venue", "")),
            str(row.get("publisher", "")),
            str(row.get("publication_type", "")),
            str(row.get("raw_meta", "")),
            str(row.get("url", "")),
        ]
    )
    venue_hit = contains_any(venue_text, venue_terms)
    if venue_hit:
        return False, f"venue_exclude:{venue_hit}"

    return True, "kept"


def render_md(title: str, rows: List[Dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('title', '')}",
                f"- Paper ID: {row.get('paper_id', '')}",
                f"- Venue: {row.get('venue', '') or 'unknown'}",
                f"- Publisher: {row.get('publisher', '') or 'unknown'}",
                f"- Year: {row.get('year', '') or 'unknown'}",
                f"- Publication Type: {row.get('publication_type', '') or 'unknown'}",
                f"- Citation Count: {row.get('citation_count', 0)}",
                f"- DOI: {row.get('doi', '') or 'unknown'}",
                f"- URL: {row.get('url', '') or 'unknown'}",
                f"- Metadata Source: {row.get('metadata_source', '') or row.get('enrichment_source', '') or 'unknown'}",
                f"- Prefilter Reason: {row.get('prefilter_reason', '')}",
                f"- Abstract/Snippet: {row.get('abstract', '') or 'unknown'}",
                "",
            ]
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Stage1 Step2b: local prefilter by title, venue, and year.")
    parser.add_argument("--planning", default=str(ROOT / "temp/stage1/step1/planning.json"))
    parser.add_argument("--candidates", default=str(ROOT / "temp/stage1/step2/candidate_pool_enriched.jsonl"))
    args = parser.parse_args()

    planning = load_json(Path(args.planning))
    rows = read_jsonl(Path(args.candidates))
    out_dir = stage_temp("stage1", "step2")

    kept = []
    rejected = []
    for row in rows:
        keep, reason = filter_row(row, planning)
        merged = dict(row)
        merged["prefilter_keep"] = keep
        merged["prefilter_reason"] = reason
        if keep:
            kept.append(merged)
        else:
            rejected.append(merged)

    summary = {
        "input_count": len(rows),
        "kept_count": len(kept),
        "rejected_count": len(rejected),
        "planning": {
            "title_exclude_terms": planning.get("retrieval", {}).get("title_exclude_terms", []),
            "venue_block_terms": planning.get("retrieval", {}).get("venue_block_terms", []),
            "year_range": planning.get("year_range", {}),
        },
    }
    dump_json(out_dir / "prefilter_summary.json", summary)
    write_jsonl(out_dir / "candidate_pool_pre_filtered.jsonl", kept)
    write_jsonl(out_dir / "candidate_pool_rejected.jsonl", rejected)
    write_text(out_dir / "candidate_pool_pre_filtered.md", render_md("Candidate Pool Pre Filtered", kept))
    write_text(out_dir / "candidate_pool_rejected.md", render_md("Candidate Pool Rejected", rejected))
    print(f"Prefilter kept {len(kept)}/{len(rows)} candidates into {out_dir / 'candidate_pool_pre_filtered.jsonl'}")


if __name__ == "__main__":
    main()
