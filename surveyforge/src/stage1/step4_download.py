import argparse
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import browser_cookie3
import http.cookiejar
import requests
from playwright.sync_api import sync_playwright

from ..core.io import ROOT, dump_json, read_jsonl, stage_temp, write_jsonl, write_text
from ..core.llm_client import load_env_file
from ..core.text import normalize_space, slugify


PAPERS_DIR = ROOT / "papers"
IEEE_DIR = PAPERS_DIR / "ieee"
MDPI_DIR = PAPERS_DIR / "mdpi"
MANUAL_DIR = PAPERS_DIR / "manual"


def log(message: str) -> None:
    print(message, flush=True)


def sanitize_filename(name: str, max_len: int = 120) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "", normalize_space(name))
    text = re.sub(r"\s+", "_", text).strip("_.")
    return text[:max_len] or "paper"


def prefixed_filename(index: int, row: Dict[str, Any], ext: str = ".pdf") -> str:
    title = row.get("title") or row.get("paper_id") or "paper"
    return f"{index:03d}_{sanitize_filename(title)}{ext}"


def doi_url(doi: str) -> str:
    doi = normalize_space(doi)
    return f"https://doi.org/{doi}" if doi else ""


def infer_source(row: Dict[str, Any]) -> str:
    publisher = normalize_space(row.get("publisher", "")).lower()
    venue = normalize_space(row.get("venue", "")).lower()
    url = normalize_space(row.get("url", "")).lower()
    doi = normalize_space(row.get("doi", "")).lower()
    reason = normalize_space(row.get("selection_reason", "")).lower()

    if "ieee" in publisher or "ieee" in venue or "ieeexplore" in url or doi.startswith("10.1109/"):
        return "ieee"
    if "mdpi" in publisher or "mdpi" in venue or "mdpi.com" in url or doi.startswith("10.3390/") or "mdpi sensors" in reason:
        return "mdpi"
    if "elsevier" in publisher or "sciencedirect" in url or doi.startswith("10.1016/"):
        return "elsevier"
    return "other"


def render_results_md(title: str, rows: List[Dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('title', '')}",
                f"- Paper ID: {row.get('paper_id', '')}",
                f"- Source Type: {row.get('source_type', '')}",
                f"- Venue: {row.get('venue', '') or 'unknown'}",
                f"- Publisher: {row.get('publisher', '') or 'unknown'}",
                f"- DOI: {row.get('doi', '') or 'unknown'}",
                f"- URL: {row.get('url', '') or 'unknown'}",
                f"- Download Status: {row.get('download_status', '')}",
                f"- Local File: {row.get('local_file', '') or 'none'}",
                f"- PDF URL: {row.get('pdf_url', '') or 'none'}",
                f"- Note: {row.get('download_note', '') or ''}",
                "",
            ]
        )
    return "\n".join(lines)


def render_manual_md(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "# Manual Download Queue",
        "",
        f"- Manual PDF directory: {MANUAL_DIR}",
        "- Download each PDF manually and save it with the suggested filename/path below.",
        "",
    ]
    for idx, row in enumerate(rows, 1):
        lines.extend(
            [
                f"## {idx}. {row.get('title', '')}",
                f"- Source Type: {row.get('source_type', '')}",
                f"- Venue: {row.get('venue', '') or 'unknown'}",
                f"- DOI: {row.get('doi', '') or 'unknown'}",
                f"- DOI URL: {row.get('doi_url', '') or 'unknown'}",
                f"- URL: {row.get('url', '') or 'unknown'}",
                f"- PDF URL: {row.get('pdf_url', '') or 'unknown'}",
                f"- Suggested filename: {row.get('suggested_filename', '')}",
                f"- Manual save path: {row.get('manual_save_path', '')}",
                f"- Note: {row.get('download_note', '') or ''}",
                "",
            ]
        )
    return "\n".join(lines)


class ElsevierPdfLinkFinder:
    def __init__(self, elsevier_api_key: str = "", email: str = "user@example.com"):
        self.elsevier_api_key = elsevier_api_key
        self.email = email
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    def find(self, doi: str) -> Dict[str, Any]:
        clean = normalize_space(doi)
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
            if clean.lower().startswith(prefix):
                clean = clean[len(prefix):]
        if not clean:
            return {"success": False, "pdf_url": "", "method": ""}

        result = self._try_elsevier_api(clean)
        if result:
            return {"success": True, "pdf_url": result, "method": "elsevier_api"}
        result = self._try_unpaywall(clean)
        if result:
            return {"success": True, "pdf_url": result, "method": "unpaywall"}
        result = self._try_publisher_page(clean)
        if result:
            return {"success": True, "pdf_url": result, "method": "publisher_page"}
        return {"success": False, "pdf_url": "", "method": ""}

    def _try_elsevier_api(self, doi: str) -> Optional[str]:
        if not self.elsevier_api_key or not doi.startswith("10.1016/"):
            return None
        headers = {"X-ELS-APIKey": self.elsevier_api_key, "Accept": "application/json"}
        try:
            resp = self.session.get(f"https://api.elsevier.com/content/article/doi/{doi}", headers=headers, timeout=20)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    entry = data.get("full-text-retrieval-response", {})
                    links = entry.get("coredata", {}).get("link", [])
                    if isinstance(links, dict):
                        links = [links]
                    for link in links:
                        if link.get("@rel") == "scidir" and link.get("@href"):
                            return link.get("@href")
                except Exception:
                    pass
        except Exception:
            pass
        pii = doi.split("/")[-1] if "/" in doi else ""
        return f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft" if pii else None

    def _try_unpaywall(self, doi: str) -> Optional[str]:
        try:
            resp = self.session.get(f"https://api.unpaywall.org/v2/{doi}?email={self.email}", timeout=15)
            if resp.status_code != 200:
                return None
            data = resp.json()
            best = data.get("best_oa_location") or {}
            if best.get("url_for_pdf"):
                return best["url_for_pdf"]
            for loc in data.get("oa_locations", []):
                if loc.get("url_for_pdf"):
                    return loc["url_for_pdf"]
        except Exception:
            return None
        return None

    def _try_publisher_page(self, doi: str) -> Optional[str]:
        try:
            resp = self.session.get(f"https://doi.org/{doi}", allow_redirects=True, timeout=20)
            page_url = resp.url
            html = resp.text
        except Exception:
            return None
        pii = re.search(r"/pii/(\w+)", page_url)
        if pii:
            return f"https://www.sciencedirect.com/science/article/pii/{pii.group(1)}/pdfft"
        m = re.search(r'href="(/science/article/pii/[^"]+/pdfft[^"]*)"', html)
        if m:
            return requests.compat.urljoin(page_url, m.group(1))
        return None


def load_ieee_browser_cookies(browser_name: str = "chrome"):
    loader = {
        "chrome": browser_cookie3.chrome,
        "chromium": browser_cookie3.chromium,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "brave": browser_cookie3.brave,
    }.get(browser_name, browser_cookie3.chrome)
    jar = loader(domain_name=".ieee.org")
    try:
        jar2 = loader(domain_name=".ieeexplore.ieee.org")
        for cookie in jar2:
            jar.set_cookie(cookie)
    except Exception:
        pass
    return jar


def ieee_session(browser_name: str = "chrome") -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://ieeexplore.ieee.org/",
        }
    )
    try:
        session.cookies = load_ieee_browser_cookies(browser_name)
    except Exception:
        pass
    return session


def ieee_article_number_from_url(url: str) -> str:
    match = re.search(r"/document/(\d+)", url or "")
    return match.group(1) if match else ""


def ieee_article_number_from_doi(session: requests.Session, doi: str) -> str:
    try:
        resp = session.get(f"https://doi.org/{doi}", allow_redirects=True, timeout=20)
        return ieee_article_number_from_url(resp.url)
    except Exception:
        return ""


def ieee_extract_pdf_link(html_text: str) -> str:
    patterns = [
        r'(https?://ieeexplore\.ieee\.org/stampPDF/getPDF\.jsp\?[^"\'<>\s]+)',
        r'["\'](/stampPDF/getPDF\.jsp\?[^"\'<>\s]+)["\']',
        r'<iframe[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'href=["\']([^"\']*\.pdf[^"\']*)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text)
        if match:
            link = match.group(1).replace("&amp;", "&")
            return "https://ieeexplore.ieee.org" + link if link.startswith("/") else link
    return ""


def is_pdf_response(resp: requests.Response) -> bool:
    return "application/pdf" in (resp.headers.get("Content-Type", "")).lower() or resp.content[:5] == b"%PDF-"


def ieee_download(row: Dict[str, Any], output_path: Path, browser_name: str = "chrome") -> Dict[str, Any]:
    session = ieee_session(browser_name)
    article_num = ieee_article_number_from_url(row.get("url", "")) or ieee_article_number_from_doi(session, row.get("doi", ""))
    if not article_num:
        return {"success": False, "pdf_url": "", "note": "failed to resolve IEEE article number"}
    stamp_url = f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={article_num}"
    try:
        resp = session.get(stamp_url, allow_redirects=True, timeout=120)
        if is_pdf_response(resp):
            output_path.write_bytes(resp.content)
            return {"success": True, "pdf_url": stamp_url, "note": "downloaded from stamp"}
        pdf_link = ieee_extract_pdf_link(resp.text)
        if pdf_link:
            resp2 = session.get(pdf_link, allow_redirects=True, timeout=120)
            if is_pdf_response(resp2):
                output_path.write_bytes(resp2.content)
                return {"success": True, "pdf_url": pdf_link, "note": "downloaded from embedded pdf link"}
            return {"success": False, "pdf_url": pdf_link, "note": "embedded IEEE PDF link returned non-pdf"}
        return {"success": False, "pdf_url": stamp_url, "note": "no PDF link found in IEEE response"}
    except Exception as exc:
        return {"success": False, "pdf_url": stamp_url, "note": str(exc)}


class MdpiBrowser:
    def __init__(self, headless: bool = True):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1600, "height": 900},
            locale="en-US",
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def resolve_doi(self, doi: str) -> str:
        page = self._context.new_page()
        try:
            page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            return page.url
        finally:
            page.close()

    def fetch_page_info(self, paper_url: str) -> Dict[str, str]:
        page = self._context.new_page()
        try:
            page.goto(paper_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            html = page.content()
            pdf_url = ""
            for pattern in [
                r'<meta\s+name=["\']fulltext_pdf["\']\s+content=["\']([^"\']+)["\']',
                r'href=["\']([^"\']*/pdf\?version=[^"\']+)["\']',
                r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\']([^"\']+)["\']',
            ]:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    pdf_url = match.group(1)
                    break
            if pdf_url.startswith("/"):
                pdf_url = "https://www.mdpi.com" + pdf_url
            title_match = re.search(r'<meta\s+name=["\']citation_title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else ""
            return {"paper_url": paper_url, "pdf_url": pdf_url, "title": title}
        finally:
            page.close()

    def download_pdf(self, pdf_url: str, output_path: Path) -> bool:
        page = self._context.new_page()
        try:
            with page.expect_download(timeout=60000) as download_info:
                page.evaluate("window.location.href = '{}';".format(pdf_url))
            download = download_info.value
            download.save_as(str(output_path))
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception:
            try:
                resp = page.goto(pdf_url, wait_until="commit", timeout=60000)
                if resp:
                    body = resp.body()
                    if body[:5] == b"%PDF-":
                        output_path.write_bytes(body)
                        return True
            except Exception:
                return False
            return False
        finally:
            page.close()


def mdpi_download(row: Dict[str, Any], output_path: Path, browser: MdpiBrowser) -> Dict[str, Any]:
    doi = normalize_space(row.get("doi", ""))
    if not doi:
        return {"success": False, "pdf_url": "", "note": "missing mdpi doi"}
    try:
        paper_url = browser.resolve_doi(doi)
        info = browser.fetch_page_info(paper_url)
        pdf_url = info.get("pdf_url") or (paper_url.rstrip("/") + "/pdf")
        ok = browser.download_pdf(pdf_url, output_path)
        return {
            "success": ok,
            "pdf_url": pdf_url,
            "note": "downloaded" if ok else "mdpi download failed",
        }
    except Exception as exc:
        return {"success": False, "pdf_url": "", "note": str(exc)}


def process_selected_papers(rows: List[Dict[str, Any]], headless: bool, browser_name: str) -> List[Dict[str, Any]]:
    IEEE_DIR.mkdir(parents=True, exist_ok=True)
    MDPI_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    elsevier_finder = ElsevierPdfLinkFinder(
        elsevier_api_key=os.environ.get("ELSEVIER_API_KEY", ""),
        email=os.environ.get("UNPAYWALL_EMAIL", "user@example.com"),
    )
    mdpi_browser = MdpiBrowser(headless=headless)
    results = []
    try:
        for idx, row in enumerate(rows, 1):
            source_type = infer_source(row)
            filename = prefixed_filename(idx, row)
            result = dict(row)
            result["source_type"] = source_type
            result["suggested_filename"] = filename
            result["manual_dir"] = str(MANUAL_DIR)
            result["manual_save_path"] = str(MANUAL_DIR / filename)
            result["doi_url"] = doi_url(row.get("doi", ""))
            result["download_status"] = ""
            result["download_note"] = ""
            result["pdf_url"] = ""
            result["local_file"] = ""

            log(f"[Step4] [{idx}/{len(rows)}] {source_type.upper()} - {row.get('title', '')[:100]}")

            if source_type == "ieee":
                output_path = IEEE_DIR / filename
                dl = ieee_download(row, output_path, browser_name=browser_name)
                result["pdf_url"] = dl.get("pdf_url", "")
                result["download_note"] = dl.get("note", "")
                if dl.get("success"):
                    result["download_status"] = "downloaded"
                    result["local_file"] = str(output_path)
                else:
                    result["download_status"] = "manual_required"
            elif source_type == "mdpi":
                output_path = MDPI_DIR / filename
                dl = mdpi_download(row, output_path, mdpi_browser)
                result["pdf_url"] = dl.get("pdf_url", "")
                result["download_note"] = dl.get("note", "")
                if dl.get("success"):
                    result["download_status"] = "downloaded"
                    result["local_file"] = str(output_path)
                else:
                    result["download_status"] = "manual_required"
            elif source_type == "elsevier":
                link = elsevier_finder.find(row.get("doi", ""))
                result["pdf_url"] = link.get("pdf_url", "")
                result["download_note"] = f"elsevier:{link.get('method', '') or 'no_pdf_link'}"
                result["download_status"] = "link_only" if link.get("success") else "manual_required"
            else:
                result["download_status"] = "manual_required"
                result["download_note"] = "non-IEEE/MDPI/Elsevier paper; provide DOI/manual download"

            results.append(result)
            time.sleep(1.0)
    finally:
        mdpi_browser.close()
    return results


def main():
    load_env_file(ROOT.parent / ".env")
    parser = argparse.ArgumentParser(description="Stage1 Step4: unified PDF downloader and manual queue generator.")
    parser.add_argument("--selected", default=str(ROOT / "temp/stage1/step3/selected_papers.jsonl"))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--browser", default="chrome")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.selected))
    out_dir = stage_temp("stage1", "step4")
    log("=" * 72)
    log("Stage1 Step4: unified downloader")
    log("=" * 72)
    log(f"[Input] Selected papers: {args.selected} ({len(rows)} papers)")
    log(f"[Output] Papers dir: {PAPERS_DIR}")
    log(f"[Output] Manual papers dir: {MANUAL_DIR}")

    results = process_selected_papers(rows, headless=args.headless, browser_name=args.browser)
    manual = [row for row in results if row.get("download_status") != "downloaded"]
    downloaded = [row for row in results if row.get("download_status") == "downloaded"]

    summary = {
        "input_count": len(rows),
        "downloaded_count": len(downloaded),
        "manual_count": len(manual),
        "by_source": {
            "ieee": sum(1 for row in results if row.get("source_type") == "ieee"),
            "mdpi": sum(1 for row in results if row.get("source_type") == "mdpi"),
            "elsevier": sum(1 for row in results if row.get("source_type") == "elsevier"),
            "other": sum(1 for row in results if row.get("source_type") == "other"),
        },
    }

    dump_json(out_dir / "download_summary.json", summary)
    write_jsonl(out_dir / "download_results.jsonl", results)
    write_jsonl(out_dir / "manual_download_queue.jsonl", manual)
    write_text(out_dir / "download_results.md", render_results_md("Download Results", results))
    write_text(out_dir / "manual_download_queue.md", render_manual_md(manual))

    log(f"[Output] Downloaded: {len(downloaded)}")
    log(f"[Output] Manual queue: {out_dir / 'manual_download_queue.jsonl'} ({len(manual)} papers)")
    if manual:
        log(f"[Output] Save manually downloaded PDFs to: {MANUAL_DIR}")
    log(f"[Output] Summary: {out_dir / 'download_summary.json'}")


if __name__ == "__main__":
    main()
