import argparse
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

from ..core.io import ROOT, dump_json, load_json, read_jsonl, stage_final, stage_temp, write_csv, write_jsonl, write_text
from ..core.llm_client import LLMClient, filter_available_providers
from ..core.text import normalize_space


CSV_FIELDS = [
    "title",
    "year",
    "venue",
    "citation_count",
    "first_author",
    "first_affiliation",
    "category",
    "research_problem",
    "research_method",
    "innovation",
    "experiment_type",
    "experiment_metric",
    "experiment_environment",
    "limitations",
]


def log(message: str) -> None:
    print(message, flush=True)


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


def load_stage1_topic(planning_path: Path) -> str:
    if not planning_path.exists():
        return "unknown survey topic"
    try:
        data = json.loads(planning_path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown survey topic"
    return normalize_space(data.get("topic", "")) or "unknown survey topic"


def group_by_category(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("category_id") or "uncategorized", []).append(row)
    return grouped


def limit_papers_per_category(grouped: Dict[str, List[Dict[str, Any]]], limit: int) -> Dict[str, List[Dict[str, Any]]]:
    if limit <= 0:
        return grouped
    return {category_id: rows[:limit] for category_id, rows in grouped.items()}


def normalize_key(text: str) -> str:
    text = normalize_space(text).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def title_tokens(text: str) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", normalize_space(text).lower()) if len(token) >= 4]


def build_pdf_index() -> List[Path]:
    return sorted((ROOT / "papers").rglob("*.pdf"))


def find_local_pdf(paper: Dict[str, Any], pdf_files: List[Path]) -> Path:
    title = paper.get("title", "")
    normalized_title = normalize_key(title)
    doi = normalize_space(paper.get("doi", "")).lower()
    doi_tail = normalize_key(doi.split("/")[-1]) if "/" in doi else normalize_key(doi)

    direct_matches = []
    for pdf_path in pdf_files:
        name_key = normalize_key(pdf_path.stem)
        if normalized_title and normalized_title in name_key:
            direct_matches.append(pdf_path)
        elif doi_tail and doi_tail in name_key:
            direct_matches.append(pdf_path)
    if direct_matches:
        return direct_matches[0]

    tokens = set(title_tokens(title))
    scored = []
    for pdf_path in pdf_files:
        name_tokens = set(title_tokens(pdf_path.stem))
        overlap = len(tokens & name_tokens)
        if overlap >= max(3, min(6, len(tokens))):
            scored.append((overlap, pdf_path))
    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return scored[0][1] if scored else Path()


def run_pdftotext(pdf_path: Path, args: List[str]) -> str:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            ["pdftotext", "-layout", "-nopgbrk", *args, str(pdf_path), str(tmp_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return tmp_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    finally:
        tmp_path.unlink(missing_ok=True)


def extract_pdf_text(pdf_path: Path, max_chars: int = 60000, enhanced_metrics: bool = False) -> str:
    if not pdf_path or not pdf_path.exists():
        return ""
    first_pages = run_pdftotext(pdf_path, ["-f", "1", "-l", "2"])
    full_text = run_pdftotext(pdf_path, [])
    if not first_pages and not full_text:
        return ""
    if enhanced_metrics:
        return select_enhanced_metric_text(full_text, first_pages, max_chars=max_chars)
    return select_relevant_text(full_text, first_pages, max_chars=max_chars)


def find_section_start(lower_text: str, names: List[str]) -> int:
    for name in names:
        match = re.search(rf"(?im)^\s*(?:[0-9ivxIVX]+[\.\)]\s*)?{re.escape(name)}\b", lower_text)
        if match:
            return match.start()
    positions = [lower_text.find(name) for name in names if lower_text.find(name) >= 0]
    return min(positions) if positions else -1


def select_relevant_text(text: str, first_pages: str = "", max_chars: int = 60000) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text)
    lower = clean.lower()
    identity_block = re.sub(r"\n{3,}", "\n\n", first_pages).strip()

    abstract_idx = find_section_start(lower, ["abstract"])
    start_idx = abstract_idx if abstract_idx >= 0 else 0
    conclusion_idx = find_section_start(lower[start_idx:], ["conclusion", "conclusions"])
    if conclusion_idx >= 0:
        conclusion_idx += start_idx
        after_conclusion = lower[conclusion_idx:]
        tail_match = re.search(
            r"(?im)^\s*(?:[0-9ivxIVX]+[\.\)]\s*)?(references|acknowledg(?:e)?ments?|appendix|author biographies?|biographies?)\b",
            after_conclusion,
        )
        end_idx = conclusion_idx + tail_match.start() if tail_match else len(clean)
    else:
        end_idx = len(clean)

    body = clean[start_idx:end_idx].strip()
    if len(body) > max_chars:
        priority_terms = [
            "experiment",
            "experimental",
            "evaluation",
            "results",
            "rmse",
            "mean error",
            "positioning error",
            "localization error",
            "cdf",
            "trajectory",
            "testbed",
            "simulation",
            "conclusion",
        ]
        chunks = [body[:12000]]
        body_lower = body.lower()
        for term in priority_terms:
            idx = body_lower.find(term)
            if idx >= 0:
                chunks.append(body[max(0, idx - 4000) : idx + 8000])
        body = "\n\n".join(chunks)

    evidence = "\n\n".join(
        [
            "=== PDF FIRST TWO PAGES / IDENTITY BLOCK ===",
            identity_block,
            "=== PDF MAIN BODY FROM ABSTRACT THROUGH CONCLUSION ===",
            body,
        ]
    ).strip()
    return evidence[:max_chars]


def select_enhanced_metric_text(text: str, first_pages: str = "", max_chars: int = 90000) -> str:
    base = select_relevant_text(text, first_pages, max_chars=52000)
    clean = re.sub(r"\n{3,}", "\n\n", text)
    lower = clean.lower()
    evidence_chunks: List[str] = []

    caption_relevance_terms = [
        "experiment",
        "experimental",
        "evaluation",
        "result",
        "performance",
        "comparison",
        "accuracy",
        "error",
        "rmse",
        "cdf",
        "p90",
        "percentile",
        "trajectory",
        "path",
        "route",
        "testbed",
        "setup",
        "scenario",
        "environment",
        "layout",
        "anchor",
        "tag",
        "sensor",
        "imu",
        "uwb",
        "ranging",
        "localization",
        "positioning",
        "nlos",
        "los",
        "dataset",
        "simulation",
    ]

    figure_table_patterns = [
        r"(?im)^\s*(fig(?:ure)?\.?\s*\d+[^\n]*(?:\n(?!\s*(?:fig(?:ure)?|table)\.?\s*\d+).{0,220}){0,4})",
        r"(?im)^\s*(table\s*[ivxlcdm\d]+[^\n]*(?:\n(?!\s*(?:fig(?:ure)?|table)\.?\s*\d+).{0,240}){0,8})",
    ]
    for pattern in figure_table_patterns:
        for match in re.finditer(pattern, clean):
            caption = match.group(1).strip()
            caption_lower = caption.lower()
            if not any(term in caption_lower for term in caption_relevance_terms):
                continue
            left = max(0, match.start() - 900)
            right = min(len(clean), match.end() + 1800)
            evidence_chunks.append(clean[left:right].strip())
            if len(evidence_chunks) >= 20:
                break
        if len(evidence_chunks) >= 20:
            break

    metric_terms = [
        "rmse",
        "root mean square",
        "mean error",
        "average error",
        "median error",
        "90%",
        "95%",
        "cdf",
        "p90",
        "percentile",
        "positioning accuracy",
        "positioning error",
        "localization accuracy",
        "localization error",
        "ranging accuracy",
        "ranging error",
        "distance error",
        "trajectory error",
        "loop closure",
        "classification accuracy",
        "nlos detection",
        "runtime",
        "latency",
        "testbed",
        "experimental setup",
        "environment",
    ]
    for term in metric_terms:
        start = 0
        while True:
            idx = lower.find(term, start)
            if idx < 0:
                break
            evidence_chunks.append(clean[max(0, idx - 1800) : min(len(clean), idx + 3200)].strip())
            start = idx + len(term)
            if len(evidence_chunks) >= 55:
                break
        if len(evidence_chunks) >= 55:
            break

    enhanced = "\n\n".join(
        [
            base,
            "=== ENHANCED FIGURE / TABLE / METRIC EVIDENCE ===",
            "\n\n---\n\n".join(chunk for chunk in evidence_chunks if chunk),
        ]
    ).strip()
    return enhanced[:max_chars]


def select_relevant_text_legacy(text: str, max_chars: int = 18000) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text)
    lower = clean.lower()
    heading_patterns = [
        "abstract",
        "index terms",
        "keywords",
        "author",
        "affiliation",
        "related work",
        "background",
        "method",
        "methodology",
        "proposed method",
        "algorithm",
        "system model",
        "implementation",
        "evaluation",
        "experiment",
        "experimental results",
        "performance evaluation",
        "results and discussion",
        "simulation",
        "numerical results",
        "discussion",
        "conclusion",
    ]
    chunks: List[str] = []
    chunks.append(clean[:4500])
    for pattern in heading_patterns:
        idx = lower.find(pattern)
        if idx >= 0:
            chunks.append(clean[idx : idx + 4500])
    metric_patterns = [
        "rmse",
        "rms error",
        "mean error",
        "average error",
        "positioning error",
        "localization error",
        "accuracy",
        "precision",
        "percentile",
        "p90",
        "runtime",
        "computation time",
        "trajectory",
        "testbed",
        "experiment setup",
    ]
    for pattern in metric_patterns:
        start = 0
        while True:
            idx = lower.find(pattern, start)
            if idx < 0:
                break
            left = max(0, idx - 1200)
            right = min(len(clean), idx + 2200)
            chunks.append(clean[left:right])
            start = idx + len(pattern)
            if len(chunks) > 32:
                break
        if len(chunks) > 32:
            break
    selected = "\n\n".join(chunks)
    return selected[:max_chars]


def compute_metadata_consistency(paper: Dict[str, Any], evidence_text: str, reading_source_type: str) -> str:
    if reading_source_type != "pdf" or not evidence_text:
        return "unknown"
    title = normalize_space(paper.get("title", "")).lower()
    if not title:
        return "unknown"
    tokens = title_tokens(title)
    if not tokens:
        return "unknown"
    evidence_lower = evidence_text.lower()
    overlap = sum(1 for token in tokens[:8] if token in evidence_lower)
    return "consistent" if overlap >= max(2, min(4, len(tokens[:8]))) else "inconsistent"


def normalize_card(card: Dict[str, Any], paper: Dict[str, Any]) -> Dict[str, Any]:
    strengths = card.get("strengths") or []
    limitations = card.get("limitations") or []
    if isinstance(strengths, str):
        strengths = [normalize_space(strengths)] if normalize_space(strengths) else []
    if isinstance(limitations, str):
        limitations = [normalize_space(limitations)] if normalize_space(limitations) else []

    metrics = card.get("metrics") or {}
    if isinstance(metrics, list):
        metrics = {}
    if not isinstance(metrics, dict):
        metrics = {}
    metric_text = card.get("metric", "")
    if not metric_text:
        metric_text = "; ".join(
            [
                f"mean_error={normalize_space(metrics.get('mean_error', ''))}" if normalize_space(metrics.get("mean_error", "")) else "",
                f"rmse={normalize_space(metrics.get('rmse', ''))}" if normalize_space(metrics.get("rmse", "")) else "",
                f"p90={normalize_space(metrics.get('p90', ''))}" if normalize_space(metrics.get("p90", "")) else "",
            ]
        )
        metric_text = "; ".join([item for item in metric_text.split("; ") if item])
    authors = paper.get("authors") or []
    metadata_first_author = paper.get("first_author") or (authors[0] if isinstance(authors, list) and authors else "")
    metadata_first_affiliation = paper.get("first_affiliation", "")
    first_author = normalize_space(card.get("first_author", "") or metadata_first_author)
    first_affiliation = normalize_space(card.get("first_affiliation", "") or metadata_first_affiliation)
    research_method = normalize_space(card.get("research_method", "") or card.get("core_idea", "") or card.get("technical_route", ""))
    experiment_metric = normalize_space(card.get("experiment_metric", "") or metric_text)
    experiment_environment = normalize_space(card.get("experiment_environment", "") or card.get("experiment_setting", ""))

    return {
        "paper_id": paper.get("paper_id", ""),
        "title": normalize_space(card.get("title", "") or paper.get("title", "")),
        "year": card.get("year", paper.get("year", "")),
        "date": normalize_space(card.get("date", "") or paper.get("publication_date", "")),
        "venue": normalize_space(card.get("venue", "") or paper.get("venue", "")),
        "citation_count": paper.get("citation_count", ""),
        "doi": normalize_space(card.get("doi", "") or paper.get("doi", "")),
        "first_author": first_author,
        "first_affiliation": first_affiliation,
        "identity_evidence": normalize_space(card.get("identity_evidence", "")),
        "research_problem": normalize_space(card.get("research_problem", "") or card.get("problem", "")),
        "problem": normalize_space(card.get("problem", "")),
        "comparison_with_related_work": normalize_space(card.get("comparison_with_related_work", "")),
        "core_idea": normalize_space(card.get("core_idea", "")),
        "research_method": research_method,
        "technical_route": normalize_space(card.get("technical_route", "") or paper.get("category_name", "")),
        "method_type": normalize_space(card.get("method_type", "")),
        "innovation": normalize_space(card.get("innovation", "")),
        "experiment_type": normalize_space(card.get("experiment_type", "")),
        "experiment_metric": experiment_metric,
        "experiment_environment": experiment_environment,
        "experiment_setting": normalize_space(card.get("experiment_setting", "")),
        "scene": normalize_space(card.get("scene", "")),
        "environment_size": normalize_space(card.get("environment_size", "")),
        "trajectory": normalize_space(card.get("trajectory", "")),
        "sensors": card.get("sensors") or [],
        "dataset": normalize_space(card.get("dataset", "")),
        "metric": normalize_space(metric_text),
        "metrics": metrics,
        "reading_source_type": normalize_space(card.get("reading_source_type", "") or paper.get("reading_source_type", "")),
        "evidence_level": normalize_space(card.get("evidence_level", "") or paper.get("evidence_level", "")),
        "metadata_consistency_flag": normalize_space(card.get("metadata_consistency_flag", "") or paper.get("metadata_consistency_flag", "") or "unknown"),
        "needs_manual_check": bool(card.get("needs_manual_check", paper.get("needs_manual_check", True))),
        "pdf_path": paper.get("pdf_path", ""),
        "main_results": normalize_space(card.get("main_results", "")),
        "strengths": strengths,
        "limitations": limitations,
        "reliability": normalize_space(card.get("reliability", "")),
        "notes": normalize_space(card.get("notes", "")),
        "needs_fulltext_confirmation": bool(card.get("needs_fulltext_confirmation", True)),
        "category_id": paper.get("category_id", ""),
        "category_name": normalize_space(card.get("category", "") or paper.get("category_name", "")),
        "url": paper.get("url", ""),
        "publisher": paper.get("publisher", ""),
        "selection_reason": paper.get("selection_reason", ""),
    }


def csv_rows_from_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for card in cards:
        rows.append(
            {
                "title": card.get("title", ""),
                "year": card.get("year", ""),
                "venue": card.get("venue", ""),
                "citation_count": card.get("citation_count", ""),
                "first_author": card.get("first_author", ""),
                "first_affiliation": card.get("first_affiliation", ""),
                "category": card.get("category_name", "") or card.get("category_id", ""),
                "research_problem": card.get("research_problem", ""),
                "research_method": card.get("research_method", ""),
                "innovation": card.get("innovation", ""),
                "experiment_type": card.get("experiment_type", ""),
                "experiment_metric": card.get("experiment_metric", ""),
                "experiment_environment": card.get("experiment_environment", ""),
                "limitations": "; ".join(card.get("limitations", []) or []),
            }
        )
    return rows


def render_cards_md(cards: List[Dict[str, Any]]) -> str:
    lines = ["# Paper Review Cards", ""]
    for idx, card in enumerate(cards, 1):
        lines.extend(
            [
                f"## {idx}. {card.get('title', '')}",
                f"- Paper ID: {card.get('paper_id', '')}",
                f"- Category: {card.get('category_name', '') or card.get('category_id', '')}",
                f"- Venue / Year: {card.get('venue', '')} / {card.get('year', '')}",
                f"- Reading Source: {card.get('reading_source_type', '')} / {card.get('evidence_level', '')}",
                f"- Metadata Consistency: {card.get('metadata_consistency_flag', '')}",
                f"- Needs Manual Check: {card.get('needs_manual_check', True)}",
                f"- PDF Path: {card.get('pdf_path', '') or 'none'}",
                f"- First Author: {card.get('first_author', '') or 'unknown'}",
                f"- First Affiliation: {card.get('first_affiliation', '') or 'unknown'}",
                f"- Research Problem: {card.get('research_problem', '')}",
                f"- Research Method: {card.get('research_method', '')}",
                f"- Method Type: {card.get('method_type', '')}",
                f"- Innovation: {card.get('innovation', '')}",
                f"- Experiment Type: {card.get('experiment_type', '')}",
                f"- Experiment Metric: {card.get('experiment_metric', '')}",
                f"- Experiment Environment: {card.get('experiment_environment', '')}",
                f"- Limitations: {'; '.join(card.get('limitations', []) or [])}",
                f"- Notes: {card.get('notes', '')}",
                "",
            ]
        )
    return "\n".join(lines)


def compact_card(card: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "paper_id": card.get("paper_id", ""),
        "title": card.get("title", ""),
        "year": card.get("year", ""),
        "venue": card.get("venue", ""),
        "citation_count": card.get("citation_count", ""),
        "first_author": card.get("first_author", ""),
        "first_affiliation": card.get("first_affiliation", ""),
        "category_id": card.get("category_id", ""),
        "category_name": card.get("category_name", ""),
        "research_problem": card.get("research_problem", ""),
        "research_method": card.get("research_method", ""),
        "method_type": card.get("method_type", ""),
        "innovation": card.get("innovation", ""),
        "experiment_type": card.get("experiment_type", ""),
        "experiment_metric": card.get("experiment_metric", ""),
        "experiment_environment": card.get("experiment_environment", ""),
        "limitations": card.get("limitations", []) or [],
    }


def compact_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [compact_card(card) for card in cards]


def render_compact_cards_md(cards: List[Dict[str, Any]]) -> str:
    lines = ["# Category Reading Cards Compact", ""]
    for idx, card in enumerate(cards, 1):
        lines.extend(
            [
                f"## {idx}. {card.get('title', '')}",
                f"- Paper ID: {card.get('paper_id', '')}",
                f"- Venue / Year: {card.get('venue', '')} / {card.get('year', '')}",
                f"- Citation Count: {card.get('citation_count', '')}",
                f"- First Author: {card.get('first_author', '')}",
                f"- First Affiliation: {card.get('first_affiliation', '')}",
                f"- Research Problem: {card.get('research_problem', '')}",
                f"- Research Method: {card.get('research_method', '')}",
                f"- Method Type: {card.get('method_type', '')}",
                f"- Innovation: {card.get('innovation', '')}",
                f"- Experiment Type: {card.get('experiment_type', '')}",
                f"- Experiment Metric: {card.get('experiment_metric', '')}",
                f"- Experiment Environment: {card.get('experiment_environment', '')}",
                f"- Limitations: {'; '.join(card.get('limitations', []) or [])}",
                "",
            ]
        )
    return "\n".join(lines)


def render_category_md(category_summaries: List[Dict[str, Any]]) -> str:
    lines = ["# Category Summaries", ""]
    for summary in category_summaries:
        lines.extend(
            [
                f"## {summary.get('category_name', '') or summary.get('category_id', '')}",
                "",
                f"- Research Goal: {summary.get('research_goal', '')}",
                f"- Main Technical Routes: {'; '.join(summary.get('main_technical_routes', []) or [])}",
                f"- Representative Innovations: {'; '.join(summary.get('representative_innovations', []) or [])}",
                f"- Common Experimental Patterns: {summary.get('common_experimental_patterns', '')}",
                f"- Strengths: {'; '.join(summary.get('strengths', []) or [])}",
                f"- Limitations: {'; '.join(summary.get('limitations', []) or [])}",
                f"- Research Gaps: {'; '.join(summary.get('research_gaps', []) or [])}",
                f"- Recommended Papers: {', '.join(summary.get('recommended_papers', []) or [])}",
                "",
                normalize_space(summary.get("narrative_summary", "")),
                "",
                "### Representative Paper Commentaries",
                "",
            ]
        )
        for item in summary.get("paper_commentaries", []) or []:
            lines.extend(
                [
                    f"- {item.get('paper_id', '')} {item.get('title', '')}: "
                    f"{normalize_space(item.get('contribution', ''))} "
                    f"Advantage: {normalize_space(item.get('advantage', ''))} "
                    f"Limitation: {normalize_space(item.get('limitation', ''))} "
                    f"Survey value: {normalize_space(item.get('survey_value', ''))}",
                ]
            )
        lines.append("")
    return "\n".join(lines)


def render_global_md(global_summary: Dict[str, Any]) -> str:
    lines = [
        "# Global Survey Summary",
        "",
        f"- Overall Scope: {global_summary.get('overall_scope', '')}",
        "",
        "## Main Findings",
        "",
    ]
    for item in global_summary.get("main_findings", []) or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Cross Category Gaps", ""])
    for item in global_summary.get("cross_category_gaps", []) or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Suggested Review Structure", ""])
    for item in global_summary.get("suggested_review_structure", []) or []:
        lines.append(f"- {item}")
    narrative = normalize_space(global_summary.get("narrative_review", ""))
    if narrative:
        lines.extend(["", "## Narrative Review", "", narrative])
    return "\n".join(lines)

def read_single_paper(client: LLMClient, paper: Dict[str, Any], pdf_files: List[Path], enhanced_metrics: bool = False) -> Dict[str, Any]:
    pdf_path = find_local_pdf(paper, pdf_files)
    evidence_text = extract_pdf_text(pdf_path, enhanced_metrics=enhanced_metrics) if pdf_path else ""
    reading_source_type = "pdf" if evidence_text else ("doi_url_title" if (paper.get("doi") or paper.get("url")) else "title_only")
    evidence_level = "fulltext" if evidence_text else ("metadata_only" if (paper.get("doi") or paper.get("url")) else "weak_inference")
    metadata_consistency_flag = compute_metadata_consistency(paper, evidence_text, reading_source_type)
    needs_manual_check = reading_source_type != "pdf" or metadata_consistency_flag == "inconsistent"

    paper_with_evidence = {
        **paper,
        "pdf_path": str(pdf_path) if pdf_path else "",
        "reading_source_type": reading_source_type,
        "evidence_level": evidence_level,
        "metadata_consistency_flag": metadata_consistency_flag,
        "needs_manual_check": needs_manual_check,
    }
    payload = {
        "paper": {
            "paper_id": paper.get("paper_id", ""),
            "title": paper.get("title", ""),
            "year": paper.get("year", ""),
            "publication_date": paper.get("publication_date", ""),
            "venue": paper.get("venue", ""),
            "publisher": paper.get("publisher", ""),
            "citation_count": paper.get("citation_count", ""),
            "doi": paper.get("doi", ""),
            "url": paper.get("url", ""),
            "authors": paper.get("authors", []),
            "first_author": paper.get("first_author", ""),
            "first_affiliation": paper.get("first_affiliation", ""),
            "category_id": paper.get("category_id", ""),
            "category_name": paper.get("category_name", ""),
            "selection_reason": paper.get("selection_reason", ""),
            "abstract": paper.get("abstract", ""),
            "reading_source_type": reading_source_type,
            "evidence_level": evidence_level,
            "metadata_consistency_flag": metadata_consistency_flag,
            "needs_manual_check": needs_manual_check,
            "pdf_path": str(pdf_path) if pdf_path else "",
            "reading_mode": "enhanced_metrics" if enhanced_metrics else "default",
            "evidence_text": evidence_text,
        },
    }
    result = client.generate_json(ROOT / "prompts/paper_reading_card.md", payload)
    return normalize_card(result if isinstance(result, dict) else {}, paper_with_evidence)


def summarize_category(client: LLMClient, topic: str, category_id: str, rows: List[Dict[str, Any]], cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {
        "topic": topic,
        "category": {
            "category_id": category_id,
            "category_name": rows[0].get("category_name", category_id) if rows else category_id,
            "paper_count": len(rows),
        },
        "paper_cards_compact": cards,
    }
    result = client.generate_json(ROOT / "prompts/category_summary.md", payload)
    summary = result if isinstance(result, dict) else {}
    return {
        "category_id": category_id,
        "category_name": normalize_space(summary.get("category_name", "") or (rows[0].get("category_name", category_id) if rows else category_id)),
        "research_goal": normalize_space(summary.get("research_goal", "")),
        "main_technical_routes": summary.get("main_technical_routes", []) or [],
        "representative_innovations": summary.get("representative_innovations", []) or [],
        "common_experimental_patterns": normalize_space(summary.get("common_experimental_patterns", "")),
        "strengths": summary.get("strengths", []) or [],
        "limitations": summary.get("limitations", []) or [],
        "research_gaps": summary.get("research_gaps", []) or [],
        "recommended_papers": summary.get("recommended_papers", []) or [],
        "paper_commentaries": summary.get("paper_commentaries", []) or [],
        "narrative_summary": normalize_space(summary.get("narrative_summary", "")),
    }


def process_category_with_client(category_id: str, rows: List[Dict[str, Any]], provider: str, client: LLMClient, enhanced_metrics: bool = False) -> Dict[str, Any]:
    temp_dir = stage_temp("stage2", category_id)
    pdf_files = build_pdf_index()
    log(f"[Stage2] Category {category_id}: {len(rows)} papers, provider={provider}")
    cards = []
    raw_outputs = []
    for idx, paper in enumerate(rows, 1):
        log(f"[Stage2]   Reading paper {idx}/{len(rows)} in {category_id}: {paper.get('title', '')[:100]}")
        pdf_path = find_local_pdf(paper, pdf_files)
        evidence_text = extract_pdf_text(pdf_path, enhanced_metrics=enhanced_metrics) if pdf_path else ""
        reading_source_type = "pdf" if evidence_text else ("doi_url_title" if (paper.get("doi") or paper.get("url")) else "title_only")
        evidence_level = "fulltext" if evidence_text else ("metadata_only" if (paper.get("doi") or paper.get("url")) else "weak_inference")
        metadata_consistency_flag = compute_metadata_consistency(paper, evidence_text, reading_source_type)
        needs_manual_check = reading_source_type != "pdf" or metadata_consistency_flag == "inconsistent"
        payload = {
            "paper": {
                "paper_id": paper.get("paper_id", ""),
                "title": paper.get("title", ""),
                "year": paper.get("year", ""),
                "publication_date": paper.get("publication_date", ""),
                "venue": paper.get("venue", ""),
                "publisher": paper.get("publisher", ""),
                "citation_count": paper.get("citation_count", ""),
                "doi": paper.get("doi", ""),
                "url": paper.get("url", ""),
                "authors": paper.get("authors", []),
                "first_author": paper.get("first_author", ""),
                "first_affiliation": paper.get("first_affiliation", ""),
                "category_id": paper.get("category_id", ""),
                "category_name": paper.get("category_name", ""),
                "selection_reason": paper.get("selection_reason", ""),
                "abstract": paper.get("abstract", ""),
                "reading_source_type": reading_source_type,
                "evidence_level": evidence_level,
                "metadata_consistency_flag": metadata_consistency_flag,
                "needs_manual_check": needs_manual_check,
                "pdf_path": str(pdf_path) if pdf_path else "",
                "reading_mode": "enhanced_metrics" if enhanced_metrics else "default",
                "evidence_text": evidence_text,
            }
        }
        raw_result = client.generate_json(ROOT / "prompts/paper_reading_card.md", payload)
        raw_outputs.append(
            {
                "paper_id": paper.get("paper_id", ""),
                "provider": provider,
                "input": payload,
                "output": raw_result,
            }
        )
        paper_with_evidence = {
            **paper,
            "pdf_path": str(pdf_path) if pdf_path else "",
            "reading_source_type": reading_source_type,
            "evidence_level": evidence_level,
            "metadata_consistency_flag": metadata_consistency_flag,
            "needs_manual_check": needs_manual_check,
        }
        card = normalize_card(raw_result if isinstance(raw_result, dict) else {}, paper_with_evidence)
        cards.append(card)
    write_jsonl(temp_dir / "paper_review_cards.jsonl", cards)
    write_jsonl(temp_dir / "paper_review_raw_outputs.jsonl", raw_outputs)
    write_text(temp_dir / "paper_review_cards.md", render_cards_md(cards))
    category_compact_cards = compact_cards(cards)
    dump_json(temp_dir / "category_reading_cards_compact.json", category_compact_cards)
    write_text(temp_dir / "category_reading_cards_compact.md", render_compact_cards_md(category_compact_cards))
    log(f"[Stage2]   Paper cards saved: {temp_dir / 'paper_review_cards.jsonl'}")
    log(f"[Stage2]   Compact category cards saved: {temp_dir / 'category_reading_cards_compact.json'}")

    return {"category_id": category_id, "rows": rows, "cards": cards, "compact_cards": category_compact_cards}


def process_category(category_id: str, rows: List[Dict[str, Any]], provider_pool: List[str], preferred_provider: str, enhanced_metrics: bool = False) -> Dict[str, Any]:
    ordered = [preferred_provider] + [provider for provider in provider_pool if provider != preferred_provider]
    last_error = None
    for provider in ordered:
        try:
            client = LLMClient(provider=provider or "auto")
            result = process_category_with_client(category_id, rows, provider, client, enhanced_metrics=enhanced_metrics)
            result["provider"] = provider
            return result
        except Exception as exc:
            last_error = exc
            log(f"[Stage2] Category {category_id}: provider={provider} failed, trying fallback if available: {exc}")
    raise RuntimeError(f"All providers failed for category {category_id}: {last_error}")


def summarize_all_categories(main_provider: str, topic: str, category_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    client = LLMClient(provider=main_provider or "auto")
    summaries = []
    log(f"[Stage2] Category summaries will use main provider={main_provider}")
    for item in category_results:
        category_id = item["category_id"]
        temp_dir = stage_temp("stage2", category_id)
        log(f"[Stage2]   Summarizing category {category_id} with main provider={main_provider}")
        compact = item.get("compact_cards") or compact_cards(item.get("cards", []))
        summary = summarize_category(client, topic, category_id, item.get("rows", []), compact)
        dump_json(temp_dir / "category_summary.json", summary)
        write_text(temp_dir / "category_summary.md", render_category_md([summary]))
        summaries.append(summary)
    return summaries


def polish_summaries(
    main_provider: str,
    topic: str,
    category_summaries: List[Dict[str, Any]],
    compact_cards_by_category: List[Dict[str, Any]],
) -> Dict[str, Any]:
    client = LLMClient(provider=main_provider or "auto")
    log(f"[Stage2] Running unified global review with main provider={main_provider} ...")
    return client.generate_json(
        ROOT / "prompts/review_polish.md",
        {
            "topic": topic,
            "category_summaries": category_summaries,
            "compact_cards_by_category": compact_cards_by_category,
        },
    )


def category_name_from_cards(category_id: str, cards: List[Dict[str, Any]]) -> str:
    for card in cards:
        name = normalize_space(card.get("category_name", ""))
        if name:
            return name
    return category_id


def load_existing_category_results() -> List[Dict[str, Any]]:
    stage2_temp = ROOT / "temp/stage2"
    if not stage2_temp.exists():
        return []
    results = []
    for category_dir in sorted(path for path in stage2_temp.iterdir() if path.is_dir()):
        cards_path = category_dir / "paper_review_cards.jsonl"
        compact_path = category_dir / "category_reading_cards_compact.json"
        if not cards_path.exists():
            continue
        cards = read_jsonl(cards_path)
        compact = load_json(compact_path) if compact_path.exists() else compact_cards(cards)
        category_id = category_dir.name
        rows = [
            {
                "category_id": category_id,
                "category_name": category_name_from_cards(category_id, cards),
            }
        ]
        results.append({"category_id": category_id, "rows": rows, "cards": cards, "compact_cards": compact})
    return results


def write_stage2_final_outputs(
    final_dir: Path,
    topic: str,
    provider_pool: List[str],
    category_provider_map: Dict[str, str],
    all_cards: List[Dict[str, Any]],
    compact_cards_by_category: List[Dict[str, Any]],
    polished_category_summaries: List[Dict[str, Any]],
    global_summary: Dict[str, Any],
    reading_mode: str,
) -> None:
    dump_json(final_dir / "paper_review_cards.json", all_cards)
    write_jsonl(final_dir / "paper_review_cards.jsonl", all_cards)
    write_text(final_dir / "paper_review_cards.md", render_cards_md(all_cards))

    dump_json(final_dir / "category_summaries.json", polished_category_summaries)
    write_text(final_dir / "category_summaries.md", render_category_md(polished_category_summaries))

    dump_json(final_dir / "global_summary.json", global_summary)
    write_text(final_dir / "global_summary.md", render_global_md(global_summary))

    write_csv(final_dir / "survey_table.csv", csv_rows_from_cards(all_cards), CSV_FIELDS)

    dump_json(
        final_dir / "survey_review.json",
        {
            "paper_review_cards": all_cards,
            "compact_cards_by_category": compact_cards_by_category,
            "category_summaries": polished_category_summaries,
            "global_summary": global_summary,
            "provider_pool": provider_pool,
            "main_provider": provider_pool[0],
            "category_provider_map": category_provider_map,
            "topic": topic,
            "reading_mode": reading_mode,
        },
    )


def run_summarize_only(args: argparse.Namespace) -> None:
    topic = load_stage1_topic(Path(args.planning))
    requested_provider_pool = parse_provider_list(args.provider, args.providers)
    log(f"[Provider] Requested provider pool: {', '.join(requested_provider_pool)}")
    provider_pool = filter_available_providers(requested_provider_pool, log)
    main_provider = provider_pool[0]
    final_dir = stage_final("stage2")
    category_results = load_existing_category_results()
    if not category_results:
        raise RuntimeError("No existing Stage2 category reading cards found under surveyforge/temp/stage2/*/paper_review_cards.jsonl.")

    log("=" * 72)
    log("Stage2: summarize-only")
    log("=" * 72)
    log(f"[Input] Existing categories: {len(category_results)}")
    log(f"[Input] Main provider for category/global summaries: {main_provider}")
    log(f"[Input] Survey topic from planning: {topic}")
    log(f"[Output] Directory: {final_dir}")

    all_cards = [card for item in category_results for card in item.get("cards", [])]
    compact_cards_by_category = [
        {
            "category_id": item.get("category_id", ""),
            "category_name": (item.get("rows", [{}])[0].get("category_name", item.get("category_id", "")) if item.get("rows") else item.get("category_id", "")),
            "paper_count": len(item.get("compact_cards", []) or []),
            "paper_cards_compact": item.get("compact_cards", []) or compact_cards(item.get("cards", [])),
        }
        for item in category_results
    ]

    category_summaries = summarize_all_categories(main_provider, topic, category_results)
    polished = polish_summaries(main_provider, topic, category_summaries, compact_cards_by_category)
    polished_category_summaries = polished.get("category_summaries", category_summaries) if isinstance(polished, dict) else category_summaries
    global_summary = polished.get("global_summary", {}) if isinstance(polished, dict) else {}
    category_provider_map = {item.get("category_id", ""): main_provider for item in category_results}

    write_stage2_final_outputs(
        final_dir,
        topic,
        provider_pool,
        category_provider_map,
        all_cards,
        compact_cards_by_category,
        polished_category_summaries,
        global_summary,
        "summarize_only",
    )

    log(f"[Output] Category summaries: {final_dir / 'category_summaries.json'}")
    log(f"[Output] Global summary: {final_dir / 'global_summary.json'}")
    log(f"[Output] CSV: {final_dir / 'survey_table.csv'}")
    log("Stage2 summarize-only finished.")


def main():
    parser = argparse.ArgumentParser(description="Stage2: category-parallel paper reading, category summary, and unified polish.")
    parser.add_argument("--selected", default=str(ROOT / "temp/stage1/step3/selected_papers.jsonl"))
    parser.add_argument("--provider", default="")
    parser.add_argument("--providers", default="")
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--papers-per-category", type=int, default=0)
    parser.add_argument("--planning", default=str(ROOT / "temp/stage1/step1/planning.json"))
    parser.add_argument(
        "--enhanced-metrics",
        action="store_true",
        help="Include additional figure/table/metric evidence from PDFs to improve Experiment Metric extraction.",
    )
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="Skip PDF reading and regenerate category/global summaries from existing Stage2 reading cards.",
    )
    args = parser.parse_args()

    if args.summarize_only:
        run_summarize_only(args)
        return

    topic = load_stage1_topic(Path(args.planning))
    selected = read_jsonl(Path(args.selected))
    grouped = limit_papers_per_category(group_by_category(selected), args.papers_per_category)
    category_items = sorted(grouped.items(), key=lambda item: item[0])
    requested_provider_pool = parse_provider_list(args.provider, args.providers)
    log(f"[Provider] Requested provider pool: {', '.join(requested_provider_pool)}")
    provider_pool = filter_available_providers(requested_provider_pool, log)
    category_provider_map = {
        category_id: provider_pool[idx % len(provider_pool)]
        for idx, (category_id, _) in enumerate(category_items)
    }
    final_dir = stage_final("stage2")
    log("=" * 72)
    log("Stage2: category-parallel survey reading")
    log("=" * 72)
    log(f"[Input] Selected papers: {args.selected} ({len(selected)} papers)")
    log(f"[Input] Categories: {len(grouped)}")
    log(f"[Input] Provider pool: {', '.join(provider_pool)}")
    log(f"[Input] Main provider for category/global summaries: {provider_pool[0]}")
    log(f"[Input] Survey topic from planning: {topic}")
    log(f"[Input] Max workers: {args.max_workers}")
    log(f"[Input] Papers per category: {args.papers_per_category or 'all'}")
    log(f"[Input] Reading mode: {'enhanced_metrics' if args.enhanced_metrics else 'default'}")
    log(f"[Output] Directory: {final_dir}")
    for category_id, provider_name in category_provider_map.items():
        log(f"[Assign] {category_id} -> {provider_name}")

    category_results = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                process_category,
                category_id,
                rows,
                provider_pool,
                category_provider_map[category_id],
                args.enhanced_metrics,
            ): category_id
            for category_id, rows in category_items
        }
        for future in as_completed(futures):
            category_id = futures[future]
            result = future.result()
            category_results.append(result)
            log(f"[Stage2] Category finished: {category_id}")

    category_results.sort(key=lambda item: item["category_id"])
    all_cards = [card for item in category_results for card in item["cards"]]
    compact_cards_by_category = [
        {
            "category_id": item.get("category_id", ""),
            "category_name": (item.get("rows", [{}])[0].get("category_name", item.get("category_id", "")) if item.get("rows") else item.get("category_id", "")),
            "paper_count": len(item.get("compact_cards", []) or []),
            "paper_cards_compact": item.get("compact_cards", []) or compact_cards(item.get("cards", [])),
        }
        for item in category_results
    ]
    category_summaries = summarize_all_categories(provider_pool[0], topic, category_results)

    polished = polish_summaries(provider_pool[0], topic, category_summaries, compact_cards_by_category)
    polished_category_summaries = polished.get("category_summaries", category_summaries) if isinstance(polished, dict) else category_summaries
    global_summary = polished.get("global_summary", {}) if isinstance(polished, dict) else {}

    write_stage2_final_outputs(
        final_dir,
        topic,
        provider_pool,
        category_provider_map,
        all_cards,
        compact_cards_by_category,
        polished_category_summaries,
        global_summary,
        "enhanced_metrics" if args.enhanced_metrics else "default",
    )

    log(f"[Output] Paper cards: {final_dir / 'paper_review_cards.jsonl'}")
    log(f"[Output] Category summaries: {final_dir / 'category_summaries.json'}")
    log(f"[Output] Global summary: {final_dir / 'global_summary.json'}")
    log(f"[Output] CSV: {final_dir / 'survey_table.csv'}")
    log("Stage2 finished.")


if __name__ == "__main__":
    main()
