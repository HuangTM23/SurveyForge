import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from ..core.io import ROOT, dump_json, read_jsonl, stage_temp, write_jsonl, write_text
from ..core.text import normalize_space


def log(message: str) -> None:
    print(message, flush=True)


def abstract_from_openalex(inverted: Dict[str, List[int]]) -> str:
    if not inverted:
        return ""
    positions = []
    for word, indexes in inverted.items():
        for idx in indexes:
            positions.append((idx, word))
    positions.sort(key=lambda item: item[0])
    return normalize_space(" ".join(word for _, word in positions))


def strip_html(text: str) -> str:
    return normalize_space(re.sub(r"<.*?>", "", text or ""))


def normalize_publisher(raw: str) -> str:
    raw = normalize_space(raw)
    mappings = {
        "Electrical and Electronics Engineers": "IEEE",
        "Institute of Electrical and Electronics Engineers": "IEEE",
        "Elsevier": "Elsevier",
        "Multidisciplinary Digital Publishing Institute": "MDPI",
        "MDPI": "MDPI",
        "Association for Computing Machinery": "ACM",
        "Springer": "Springer",
        "Wiley": "Wiley",
    }
    for key, value in mappings.items():
        if key.lower() in raw.lower():
            return value
    return raw


def normalize_doi(doi: str) -> str:
    return normalize_space(doi).replace("https://doi.org/", "").replace("http://doi.org/", "")


def infer_publication_type(venue: str, source_type: str = "", raw_type: str = "") -> str:
    text = normalize_space(f"{venue} {source_type} {raw_type}").lower()
    conference_terms = [
        "conference",
        "proceedings",
        "symposium",
        "workshop",
        "congress",
        "international conference",
        "ieee international",
        "acm international",
        "icra",
        "iros",
        "ipin",
        "icc",
        "globecom",
    ]
    journal_terms = ["journal", "transactions", "letters", "sensors", "measurement", "automation in construction"]
    if any(term in text for term in conference_terms):
        return "conference"
    if source_type == "journal" or any(term in text for term in journal_terms):
        return "journal"
    if raw_type in {"proceedings-article", "paper-conference"}:
        return "conference"
    inferred = raw_type or source_type or "unknown"
    return "conference" if inferred == "unknown" else inferred


def fetch_openalex_by_title(title: str, year: Any = None) -> Dict[str, Any]:
    params = {
        "filter": f'title.search:"{title}"',
        "per-page": 1,
        "select": "id,doi,display_name,publication_year,publication_date,cited_by_count,abstract_inverted_index,primary_location,authorships,type",
    }
    if year:
        params["filter"] = f'{params["filter"]},publication_year:{year}'
    response = requests.get("https://api.openalex.org/works", params=params, timeout=30)
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return {}
    item = results[0]
    return parse_openalex_work(item)


def parse_openalex_work(item: Dict[str, Any]) -> Dict[str, Any]:
    primary_location = item.get("primary_location") or {}
    host = primary_location.get("source") or {}
    venue = host.get("display_name") or ""
    source_type = host.get("type") or ""
    authors = []
    first_affiliation = ""
    for authorship in item.get("authorships", []) or []:
        author = authorship.get("author") or {}
        name = author.get("display_name", "")
        if name:
            authors.append(name)
        if not first_affiliation:
            for institution in authorship.get("institutions", []) or []:
                display_name = normalize_space(institution.get("display_name", ""))
                if display_name:
                    first_affiliation = display_name
                    break
    oa = item.get("open_access") or {}
    return {
        "title": item.get("display_name") or "",
        "doi": normalize_doi(item.get("doi") or ""),
        "venue": host.get("display_name") or "",
        "publisher": normalize_publisher(host.get("host_organization_name") or ""),
        "year": item.get("publication_year"),
        "publication_date": item.get("publication_date") or "",
        "citation_count": item.get("cited_by_count") or 0,
        "abstract": abstract_from_openalex(item.get("abstract_inverted_index") or {}),
        "openalex_id": item.get("id") or "",
        "openalex_title": item.get("display_name") or "",
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "first_affiliation": first_affiliation,
        "url": item.get("id") or "",
        "pdf_url": oa.get("oa_url") or primary_location.get("pdf_url") or "",
        "publication_type": infer_publication_type(venue, source_type, item.get("type") or ""),
        "metadata_source": "openalex",
    }


def fetch_crossref_by_title(title: str) -> Dict[str, Any]:
    params = {
        "query.bibliographic": title,
        "rows": 1,
        "sort": "score",
        "order": "desc",
    }
    response = requests.get("https://api.crossref.org/works", params=params, timeout=30)
    response.raise_for_status()
    items = response.json().get("message", {}).get("items", [])
    return parse_crossref_item(items[0]) if items else {}


def parse_crossref_item(item: Dict[str, Any]) -> Dict[str, Any]:
    title = item.get("title", [""])[0] if item.get("title") else ""
    container = item.get("container-title", [])
    venue = container[0] if container else ""
    issued = item.get("issued", {}) or {}
    parts = issued.get("date-parts", [[]])
    year = parts[0][0] if parts and parts[0] else 0
    date_parts = parts[0] if parts else []
    publication_date = "-".join(str(part) for part in date_parts) if date_parts else ""
    authors = []
    first_affiliation = ""
    for author in item.get("author", []) or []:
        name = normalize_space(f"{author.get('given', '')} {author.get('family', '')}")
        if name:
            authors.append(name)
        if not first_affiliation:
            for affiliation in author.get("affiliation", []) or []:
                affiliation_name = normalize_space(affiliation.get("name", ""))
                if affiliation_name:
                    first_affiliation = affiliation_name
                    break
    return {
        "title": title,
        "doi": normalize_doi(item.get("DOI", "")),
        "venue": venue,
        "publisher": normalize_publisher(item.get("publisher", "")),
        "year": year,
        "publication_date": publication_date,
        "citation_count": item.get("is-referenced-by-count") or 0,
        "abstract": strip_html(item.get("abstract", "")),
        "openalex_id": "",
        "openalex_title": "",
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "first_affiliation": first_affiliation,
        "url": item.get("URL", ""),
        "pdf_url": "",
        "publication_type": infer_publication_type(venue, "", item.get("type", "")),
        "metadata_source": "crossref",
    }


def fetch_metadata_by_title(title: str, year: Any = None) -> Dict[str, Any]:
    try:
        openalex = fetch_openalex_by_title(title, year)
        if openalex:
            return openalex
    except Exception as exc:
        log(f"[Step2b]   OpenAlex failed: {exc}")
    try:
        crossref = fetch_crossref_by_title(title)
        if crossref:
            return crossref
    except Exception as exc:
        log(f"[Step2b]   Crossref failed: {exc}")
    return {}


def enrich_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    enriched = []
    rejected = []
    log(f"[Step2b] Metadata enrichment before prefilter: {len(rows)} Google Scholar titles")
    for idx, row in enumerate(rows, 1):
        title = normalize_space(row.get("title", ""))
        log(f"[Step2b] Enriching {idx}/{len(rows)} title: {title[:90]}")
        extra = fetch_metadata_by_title(title, row.get("year"))
        if not extra:
            rejected.append({**row, "enrichment_keep": False, "enrichment_reason": "not_found_in_openalex_or_crossref"})
            log("[Step2b]   dropped: not found in OpenAlex or Crossref")
            continue
        merged = dict(row)
        for key in ["title", "doi", "venue", "publisher", "year", "publication_date", "citation_count", "abstract", "url"]:
            merged[f"raw_{key}"] = row.get(key)
            merged[key] = extra.get(key)
        for key in ["openalex_id", "openalex_title", "authors", "first_author", "first_affiliation", "pdf_url", "publication_type", "metadata_source"]:
            merged[key] = extra.get(key)
        merged["enrichment_keep"] = True
        merged["enrichment_source"] = extra.get("metadata_source", "")
        merged["scholar_title"] = title
        enriched.append(merged)
        log(
            "[Step2b]   matched via "
            f"{merged.get('metadata_source')}: "
            f"venue={merged.get('venue') or 'unknown'}, "
            f"type={merged.get('publication_type') or 'unknown'}, "
            f"doi={merged.get('doi') or 'unknown'}"
        )
    return enriched, rejected


def render_md(rows: List[Dict[str, Any]]) -> str:
    lines = ["# Candidate Pool Enriched", ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('title', '')}",
                f"- Paper ID: {row.get('paper_id', '')}",
                f"- Venue: {row.get('venue', '') or 'unknown'}",
                f"- Publisher: {row.get('publisher', '') or 'unknown'}",
                f"- Year: {row.get('year', '') or 'unknown'}",
                f"- Publication Date: {row.get('publication_date', '') or 'unknown'}",
                f"- Citation Count: {row.get('citation_count', 0)}",
                f"- DOI: {row.get('doi', '') or 'unknown'}",
                f"- Publication Type: {row.get('publication_type', '') or 'unknown'}",
                f"- Metadata Source: {row.get('metadata_source', '') or row.get('enrichment_source', '') or 'unknown'}",
                f"- URL: {row.get('url', '') or 'unknown'}",
                f"- PDF URL: {row.get('pdf_url', '') or 'unknown'}",
                f"- OpenAlex ID: {row.get('openalex_id', '') or 'unknown'}",
                f"- Abstract: {row.get('abstract', '') or 'unknown'}",
                "",
            ]
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Stage1 Step2b: enrich Scholar titles with OpenAlex first, Crossref fallback, drop unresolved titles.")
    parser.add_argument("--candidates", default=str(ROOT / "temp/stage1/step2/candidate_pool_raw.jsonl"))
    args = parser.parse_args()

    rows = read_jsonl(Path(args.candidates))
    out_dir = stage_temp("stage1", "step2")
    enriched, rejected = enrich_rows(rows)
    write_jsonl(out_dir / "candidate_pool_enriched.jsonl", enriched)
    write_text(out_dir / "candidate_pool_enriched.md", render_md(enriched))
    write_jsonl(out_dir / "candidate_pool_enrichment_rejected.jsonl", rejected)
    write_text(out_dir / "candidate_pool_enrichment_rejected.md", render_md(rejected))
    dump_json(
        out_dir / "enrichment_summary.json",
        {
            "input_count": len(rows),
            "kept_count": len(enriched),
            "rejected_count": len(rejected),
            "policy": "OpenAlex first, Crossref fallback, drop unresolved titles",
        },
    )
    log(f"[Output] Enriched pool: {out_dir / 'candidate_pool_enriched.jsonl'} ({len(enriched)} papers)")
    log(f"[Output] Enrichment rejected: {out_dir / 'candidate_pool_enrichment_rejected.jsonl'} ({len(rejected)} papers)")


if __name__ == "__main__":
    main()
