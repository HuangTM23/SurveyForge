import argparse
import hashlib
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
import requests

from ..core.io import ROOT, dump_json, load_json, stage_temp, write_jsonl, write_text
from ..core.text import normalize_space


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15",
]

SCHOLAR_SOURCES = [
    {
        "name": "google_scholar",
        "url": "https://scholar.google.com/scholar",
    },
    {
        "name": "cntpj_scholar_mirror",
        "url": "https://xs.cntpj.com/schhp",
    },
]


def paper_id(title: str, url: str = "") -> str:
    key = normalize_space(title).lower() or normalize_space(url).lower()
    return "p_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def extract_year(text: str) -> Any:
    match = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(match.group(0)) if match else None


def extract_citations(text: str) -> int:
    match = re.search(r"Cited by\s+(\d+)", text or "", re.I)
    return int(match.group(1)) if match else 0


def extract_doi(text: str) -> str:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text or "")
    return match.group(0).rstrip(".,;") if match else ""


def parse_scholar_result(result: Any, query: str, source_name: str) -> Dict[str, Any]:
    title_elem = result.find("h3", class_="gs_rt")
    if not title_elem:
        return {}
    title = title_elem.get_text(" ", strip=True)
    while title.startswith("[") and "]" in title:
        title = re.sub(r"^\[.*?\]\s*", "", title).strip()
    link_elem = title_elem.find("a")
    url = link_elem.get("href", "") if link_elem else ""

    meta_elem = result.find("div", class_="gs_a")
    meta_text = meta_elem.get_text(" ", strip=True) if meta_elem else ""
    snippet_elem = result.find("div", class_="gs_rs")
    snippet = snippet_elem.get_text(" ", strip=True) if snippet_elem else ""
    venue = ""
    if " - " in meta_text:
        parts = [normalize_space(part) for part in meta_text.split(" - ") if normalize_space(part)]
        venue = parts[-2] if len(parts) >= 2 else parts[-1]
    return {
        "paper_id": paper_id(title, url),
        "title": title,
        "year": extract_year(meta_text),
        "venue": venue,
        "citation_count": extract_citations(result.get_text(" ", strip=True)),
        "doi": extract_doi(result.get_text(" ", strip=True)),
        "url": url,
        "abstract": snippet,
        "source": source_name,
        "query": query,
        "raw_meta": meta_text,
    }


def fetch_google_scholar_page(
    session: requests.Session,
    source: Dict[str, str],
    query: str,
    year_from: int,
    year_to: int,
    start: int,
) -> List[Dict[str, Any]]:
    params = {
        "q": query,
        "hl": "en",
        "as_ylo": year_from,
        "as_yhi": year_to,
        "start": start,
    }
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    response = session.get(source["url"], params=params, headers=headers, timeout=30)
    if response.status_code in {403, 429} or "sorry/index" in response.url or "captcha" in response.text.lower():
        raise RuntimeError(f"{source['name']} blocked the request with HTTP {response.status_code}.")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    for result in soup.find_all("div", class_="gs_ri"):
        row = parse_scholar_result(result, query, source["name"])
        if row.get("title"):
            rows.append(row)
    return rows


def fetch_from_source(
    source: Dict[str, str],
    query: str,
    year_from: int,
    year_to: int,
    max_results: int,
    delay: float,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    session = requests.Session()
    pages = max(1, (max_results + 9) // 10)
    for page in range(pages):
        start = page * 10
        page_rows = fetch_google_scholar_page(session, source, query, year_from, year_to, start)
        if not page_rows:
            break
        for row in page_rows:
            rows.append(row)
            if len(rows) >= max_results:
                return rows
        time.sleep(delay + random.uniform(1.0, 3.0))
    return rows


def fetch_google_scholar(query: str, year_from: int, year_to: int, max_results: int, delay: float) -> Dict[str, Any]:
    errors = {}
    for source in SCHOLAR_SOURCES:
        try:
            rows = fetch_from_source(source, query, year_from, year_to, max_results, delay)
            if rows:
                return {"rows": rows, "active_source": source["name"], "errors": errors}
            errors[source["name"]] = "No results returned."
        except Exception as exc:
            errors[source["name"]] = str(exc)
    return {"rows": [], "active_source": "", "errors": errors}


def build_search_url(source: Dict[str, str], query: str, year_from: int, year_to: int, start: int = 0) -> str:
    params = {
        "q": query,
        "hl": "en",
        "as_sdt": "0,47",
        "as_ylo": year_from,
        "as_yhi": year_to,
        "start": start,
    }
    return f"{source['url']}?{urlencode(params)}"


def parse_scholar_html(html: str, query: str, source_name: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for result in soup.find_all("div", class_="gs_ri"):
        row = parse_scholar_result(result, query, source_name)
        if row.get("title"):
            rows.append(row)
    return rows


def find_next_url(html: str, current_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    nav = soup.find(id="gs_n")
    if nav:
        candidates.extend(nav.find_all("a", href=True))
    candidates.extend(soup.find_all("a", href=True, attrs={"aria-label": re.compile("next", re.I)}))
    candidates.extend(soup.find_all("a", href=True, string=re.compile(r"^\s*(Next|下一页)\s*$", re.I)))
    for link in candidates:
        text = normalize_space(link.get_text(" ", strip=True))
        aria = normalize_space(link.get("aria-label", ""))
        href = link.get("href", "")
        if "start=" in href and (text.lower() == "next" or "next" in aria.lower() or text == "下一页"):
            return urljoin(current_url, href)
    return ""


def fetch_browser_assisted(
    query: str,
    year_from: int,
    year_to: int,
    max_results: int,
    delay: float,
    start_url: str = "",
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    active_source = "browser_assisted"
    initial_url = start_url or build_search_url(SCHOLAR_SOURCES[0], query, year_from, year_to)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(initial_url, wait_until="domcontentloaded", timeout=60000)
        print("\n" + "=" * 72)
        print("Browser assisted retrieval / 浏览器辅助检索")
        print("=" * 72)
        print(f"Suggested query / 建议检索式: {query}")
        print(f"Year range / 时间范围: {year_from}-{year_to}")
        print("If Google Scholar shows captcha/429, solve it manually or navigate to a working mirror.")
        print("如果出现验证码/429，请在浏览器中手动处理；也可以手动跳转到可用镜像结果页。")
        print("When the browser shows a result list, return here and press Enter.")
        print("当浏览器已经显示论文结果列表后，回到终端按回车。")
        input("> ")

        while len(rows) < max_results:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1500)
            html = page.content()
            page_rows = parse_scholar_html(html, query, active_source)
            if not page_rows:
                print("No Scholar result nodes found on current page / 当前页未发现 Scholar 结果节点。")
                break
            for row in page_rows:
                key = row.get("paper_id")
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                print(f"[{len(rows)}/{max_results}] {row.get('title', '')[:90]}")
                if len(rows) >= max_results:
                    break
            if len(rows) >= max_results:
                break
            next_url = find_next_url(html, page.url)
            if not next_url:
                print("No next page link found / 未找到下一页链接。")
                break
            page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(int((delay + random.uniform(1.0, 3.0)) * 1000))
        browser.close()
    return {"rows": rows, "active_source": active_source, "errors": {}}


def render_md(rows: List[Dict[str, Any]]) -> str:
    lines = ["# Candidate Pool Raw", ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('title', '')}",
                f"- Paper ID: {row.get('paper_id', '')}",
                f"- Venue: {row.get('venue', '') or 'unknown'}",
                f"- Year: {row.get('year', '') or 'unknown'}",
                f"- Citation Count: {row.get('citation_count', 0)}",
                f"- DOI: {row.get('doi', '') or 'unknown'}",
                f"- URL: {row.get('url', '') or 'unknown'}",
                f"- Abstract/Snippet: {row.get('abstract', '') or 'unknown'}",
                "",
            ]
        )
    return "\n".join(lines)


def unique_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for row in rows:
        key = normalize_space(row.get("title", "")).lower() or normalize_space(row.get("url", "")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Stage1 Step2: retrieve Google Scholar candidates.")
    parser.add_argument("--planning", default=str(ROOT / "temp/stage1/step1/planning.json"))
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--mode", choices=["auto", "requests", "browser"], default="auto")
    parser.add_argument(
        "--browser-start-url",
        default="",
        help="Optional start URL for browser-assisted mode, e.g. a Scholar mirror/navigation page.",
    )
    args = parser.parse_args()

    planning = load_json(Path(args.planning))
    out_dir = stage_temp("stage1", "step2")
    retrieval = planning.get("retrieval", {})
    year_range = planning.get("year_range", {})
    search_queries = [
        normalize_space(query)
        for query in (retrieval.get("search_queries") or [])
        if normalize_space(query)
    ]
    if not search_queries:
        query = normalize_space(retrieval.get("search_query", "")) or normalize_space(planning.get("topic", ""))
        search_queries = [query] if query else []
    year_from = int(year_range.get("from", 1900))
    year_to = int(year_range.get("to", 2100))
    max_results = int(planning.get("gs_max_results", 100))
    min_results_per_query = int(planning.get("gs_min_results_per_query", 30))
    per_query_results = max_results if len(search_queries) <= 1 else min_results_per_query

    dump_json(out_dir / "retrieval_input.json", {
        "queries": search_queries,
        "query_rule": "queries are generated by Stage1 Step1 planner from the natural-language research topic",
        "year_range": {"from": year_from, "to": year_to},
        "gs_max_results": max_results,
        "gs_min_results_per_query": min_results_per_query,
        "per_query_results": per_query_results,
    })
    all_rows: List[Dict[str, Any]] = []
    statuses = []
    for idx, query in enumerate(search_queries, 1):
        print(f"[Step2] Retrieving query {idx}/{len(search_queries)}: {query}")
        if args.mode == "browser":
            result = fetch_browser_assisted(query, year_from, year_to, per_query_results, args.delay, args.browser_start_url)
        else:
            result = fetch_google_scholar(query, year_from, year_to, per_query_results, args.delay)
            if args.mode == "auto" and not result["rows"]:
                result = fetch_browser_assisted(query, year_from, year_to, per_query_results, args.delay, args.browser_start_url)
        all_rows.extend(result["rows"])
        statuses.append(
            {
                "query": query,
                "row_count": len(result["rows"]),
                "active_source": result["active_source"],
                "errors": result["errors"],
            }
        )
    rows = unique_rows(all_rows)
    retrieval_status = {
        "mode": args.mode,
        "queries": statuses,
        "sources": [source["name"] for source in SCHOLAR_SOURCES],
        "raw_row_count": len(all_rows),
        "dedup_row_count": len(rows),
    }
    if not rows:
        retrieval_status["error"] = (
            "All Scholar retrieval sources failed or returned no results. "
            "Step2 does not run API enrichment; rerun later or provide a candidate pool manually."
        )
    dump_json(out_dir / "retrieval_status.json", retrieval_status)
    write_jsonl(out_dir / "candidate_pool_raw.jsonl", rows)
    write_text(out_dir / "candidate_pool_raw.md", render_md(rows))
    print(f"Retrieved {len(rows)} candidates into {out_dir / 'candidate_pool_raw.jsonl'}")


if __name__ == "__main__":
    main()
