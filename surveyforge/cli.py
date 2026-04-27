import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from surveyforge.src.core.llm_client import check_provider


DEFAULT_PROVIDER_LIST = "chatgpt,gemini,kimi,deepseek,xiaomi_mimo"


def run_module(module: str, extra_args=None):
    cmd = [sys.executable, "-m", module]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)


def parse_provider_list(provider: str = "", providers: str = ""):
    items = []
    if providers:
        items.extend([item.strip() for item in providers.split(",") if item.strip()])
    if provider and provider not in items:
        items.insert(0, provider)
    if items:
        return items
    return [item.strip() for item in DEFAULT_PROVIDER_LIST.split(",") if item.strip()]


def preflight_providers(provider: str = "", providers: str = ""):
    requested = parse_provider_list(provider, providers)
    print("[Preflight] Checking LLM providers before one-click run...")
    available = []
    unavailable = []
    seen = set()
    for item in requested:
        if item in seen:
            continue
        seen.add(item)
        ok, message = check_provider(item)
        status = "OK" if ok else "FAILED"
        print(f"[Preflight] [{status}] {item}: {message}")
        if ok:
            available.append(item)
        else:
            unavailable.append((item, message))
    if unavailable:
        print("\n[Preflight] Some requested LLM providers are unavailable. Stop running.")
        print("[Preflight] Available providers:")
        print("  " + (",".join(available) if available else "none"))
        print("[Preflight] Please rerun with a working provider list, for example:")
        if available:
            print(f"  --provider {available[0]} --providers {','.join(available)}")
        else:
            print("  python3 surveyforge/cli.py providers")
        raise SystemExit(2)
    return available


def main():
    parser = argparse.ArgumentParser(description="SurveyForge")
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="Stage1 Step1: interactive planning.")
    plan.add_argument("--provider", default="")
    plan.add_argument("--no-interactive", action="store_true")
    plan.add_argument("--input", default="")

    retrieve = subparsers.add_parser("retrieve", help="Stage1 Step2: retrieve candidate papers.")
    retrieve.add_argument("--planning", default="")
    retrieve.add_argument("--delay", type=float, default=3.0)
    retrieve.add_argument("--mode", choices=["auto", "requests", "browser"], default="auto")
    retrieve.add_argument("--browser-start-url", default="")

    prefilter = subparsers.add_parser("prefilter", help="Stage1 Step2b: local prefilter by title, venue, and year.")
    prefilter.add_argument("--planning", default="")
    prefilter.add_argument("--candidates", default="")

    enrich = subparsers.add_parser("enrich", help="Stage1 Step2b: enrich candidate pool with OpenAlex before prefilter.")
    enrich.add_argument("--candidates", default="")

    screen = subparsers.add_parser("screen", help="Stage1 Step3: LLM filter, classify, and select papers.")
    screen.add_argument("--provider", default="")
    screen.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    screen.add_argument("--planning", default="")
    screen.add_argument("--candidates", default="")
    screen.add_argument("--batch-size", type=int, default=25)

    download = subparsers.add_parser("download", help="Stage1 Step4: create manual download queue.")
    download.add_argument("--selected", default="")
    download.add_argument("--headless", action="store_true")
    download.add_argument("--browser", default="chrome")

    clean = subparsers.add_parser("clean", help="Clean generated SurveyForge temp/final files for a fresh topic run.")
    clean.add_argument("--include-papers", action="store_true", help="Also remove downloaded PDFs under surveyforge/papers.")

    providers_cmd = subparsers.add_parser("providers", help="Check LLM provider availability.")
    providers_cmd.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    providers_cmd.add_argument("--timeout", type=int, default=45)
    providers_cmd.add_argument("--json", action="store_true")

    read = subparsers.add_parser("read", help="Stage2: generate reading cards and survey report.")
    read.add_argument("--provider", default="")
    read.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    read.add_argument("--selected", default="")
    read.add_argument("--max-workers", type=int, default=3)
    read.add_argument("--papers-per-category", type=int, default=0)
    read.add_argument("--planning", default="")
    read.add_argument("--enhanced-metrics", action="store_true")

    summarize = subparsers.add_parser("summarize", help="Stage2: regenerate category/global summaries from existing reading cards.")
    summarize.add_argument("--provider", default="")
    summarize.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    summarize.add_argument("--planning", default="")
    stage1 = subparsers.add_parser("stage1", help="Run all stage1 steps.")
    stage1.add_argument("--provider", default="")
    stage1.add_argument("--no-interactive", action="store_true")
    stage1.add_argument("--input", default="")
    stage1.add_argument("--delay", type=float, default=3.0)
    stage1.add_argument("--retrieve-mode", choices=["auto", "requests", "browser"], default="auto")
    stage1.add_argument("--browser-start-url", default="")
    stage1.add_argument("--batch-size", type=int, default=25)
    stage1.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    stage1.add_argument("--download-headless", action="store_true")
    stage1.add_argument("--download-browser", default="chrome")
    stage2 = subparsers.add_parser("stage2", help="Run stage2.")
    stage2.add_argument("--provider", default="")
    stage2.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    stage2.add_argument("--selected", default="")
    stage2.add_argument("--max-workers", type=int, default=3)
    stage2.add_argument("--papers-per-category", type=int, default=0)
    stage2.add_argument("--planning", default="")
    stage2.add_argument("--enhanced-metrics", action="store_true")

    all_cmd = subparsers.add_parser("all", help="Run all stages.")
    all_cmd.add_argument("--provider", default="")
    all_cmd.add_argument("--providers", default=DEFAULT_PROVIDER_LIST)
    all_cmd.add_argument("--no-interactive", action="store_true")
    all_cmd.add_argument("--input", default="")
    all_cmd.add_argument("--delay", type=float, default=3.0)
    all_cmd.add_argument("--retrieve-mode", choices=["auto", "requests", "browser"], default="auto")
    all_cmd.add_argument("--browser-start-url", default="")
    all_cmd.add_argument("--batch-size", type=int, default=25)
    all_cmd.add_argument("--download-headless", action="store_true")
    all_cmd.add_argument("--download-browser", default="chrome")
    all_cmd.add_argument("--max-workers", type=int, default=3)
    all_cmd.add_argument("--papers-per-category", type=int, default=0)
    all_cmd.add_argument("--planning", default="")
    all_cmd.add_argument("--enhanced-metrics", action="store_true")

    args = parser.parse_args()
    if args.command == "plan":
        extra = []
        if args.provider:
            extra.extend(["--provider", args.provider])
        if args.no_interactive:
            extra.append("--no-interactive")
        if args.input:
            extra.extend(["--input", args.input])
        run_module("surveyforge.src.stage1.step1_planner", extra)
        return
    if args.command == "retrieve":
        extra = []
        if args.planning:
            extra.extend(["--planning", args.planning])
        extra.extend(["--delay", str(args.delay)])
        extra.extend(["--mode", args.mode])
        if args.browser_start_url:
            extra.extend(["--browser-start-url", args.browser_start_url])
        run_module("surveyforge.src.stage1.step2_retrieve", extra)
        return
    if args.command == "prefilter":
        extra = []
        if args.planning:
            extra.extend(["--planning", args.planning])
        if args.candidates:
            extra.extend(["--candidates", args.candidates])
        run_module("surveyforge.src.stage1.step2_prefilter", extra)
        return
    if args.command == "enrich":
        extra = []
        if args.candidates:
            extra.extend(["--candidates", args.candidates])
        run_module("surveyforge.src.stage1.step2_enrich", extra)
        return
    if args.command == "screen":
        extra = []
        if args.provider:
            extra.extend(["--provider", args.provider])
        if args.providers:
            extra.extend(["--providers", args.providers])
        if args.planning:
            extra.extend(["--planning", args.planning])
        if args.candidates:
            extra.extend(["--candidates", args.candidates])
        extra.extend(["--batch-size", str(args.batch_size)])
        run_module("surveyforge.src.stage1.step3_screen", extra)
        return
    if args.command == "download":
        extra = []
        if args.selected:
            extra.extend(["--selected", args.selected])
        if args.headless:
            extra.append("--headless")
        if args.browser:
            extra.extend(["--browser", args.browser])
        run_module("surveyforge.src.stage1.step4_download", extra)
        return
    if args.command == "clean":
        extra = []
        if args.include_papers:
            extra.append("--include-papers")
        run_module("surveyforge.src.tools.clean_outputs", extra)
        return
    if args.command == "providers":
        extra = ["--providers", args.providers, "--timeout", str(args.timeout)]
        if args.json:
            extra.append("--json")
        run_module("surveyforge.src.tools.check_providers", extra)
        return
    if args.command in {"read", "stage2"}:
        if args.command == "stage2":
            preflight_providers(args.provider, args.providers)
        extra = []
        if args.provider:
            extra.extend(["--provider", args.provider])
        if args.providers:
            extra.extend(["--providers", args.providers])
        if args.selected:
            extra.extend(["--selected", args.selected])
        if args.planning:
            extra.extend(["--planning", args.planning])
        if args.enhanced_metrics:
            extra.append("--enhanced-metrics")
        extra.extend(["--max-workers", str(args.max_workers)])
        extra.extend(["--papers-per-category", str(args.papers_per_category)])
        run_module("surveyforge.src.stage2.read_and_report", extra)
        return
    if args.command == "summarize":
        preflight_providers(args.provider, args.providers)
        extra = ["--summarize-only"]
        if args.provider:
            extra.extend(["--provider", args.provider])
        if args.providers:
            extra.extend(["--providers", args.providers])
        if args.planning:
            extra.extend(["--planning", args.planning])
        run_module("surveyforge.src.stage2.read_and_report", extra)
        return
    if args.command == "stage1":
        preflight_providers(args.provider, args.providers)
        plan_args = []
        if args.provider:
            plan_args.extend(["--provider", args.provider])
        if args.no_interactive:
            plan_args.append("--no-interactive")
        if args.input:
            plan_args.extend(["--input", args.input])
        run_module("surveyforge.src.stage1.step1_planner", plan_args)
        retrieve_args = ["--delay", str(args.delay), "--mode", args.retrieve_mode]
        if args.browser_start_url:
            retrieve_args.extend(["--browser-start-url", args.browser_start_url])
        run_module("surveyforge.src.stage1.step2_retrieve", retrieve_args)
        run_module("surveyforge.src.stage1.step2_enrich")
        run_module("surveyforge.src.stage1.step2_prefilter")
        screen_args = ["--batch-size", str(args.batch_size)]
        if args.provider:
            screen_args.extend(["--provider", args.provider])
        if args.providers:
            screen_args.extend(["--providers", args.providers])
        run_module("surveyforge.src.stage1.step3_screen", screen_args)
        download_args = ["--browser", args.download_browser]
        if args.download_headless:
            download_args.append("--headless")
        run_module("surveyforge.src.stage1.step4_download", download_args)
        return
    if args.command == "all":
        preflight_providers(args.provider, args.providers)
        plan_args = []
        if args.provider:
            plan_args.extend(["--provider", args.provider])
        if args.no_interactive:
            plan_args.append("--no-interactive")
        if args.input:
            plan_args.extend(["--input", args.input])
        run_module("surveyforge.src.stage1.step1_planner", plan_args)
        retrieve_args = ["--delay", str(args.delay), "--mode", args.retrieve_mode]
        if args.browser_start_url:
            retrieve_args.extend(["--browser-start-url", args.browser_start_url])
        run_module("surveyforge.src.stage1.step2_retrieve", retrieve_args)
        run_module("surveyforge.src.stage1.step2_enrich")
        run_module("surveyforge.src.stage1.step2_prefilter")
        screen_args = ["--batch-size", str(args.batch_size)]
        if args.provider:
            screen_args.extend(["--provider", args.provider])
        if args.providers:
            screen_args.extend(["--providers", args.providers])
        run_module("surveyforge.src.stage1.step3_screen", screen_args)
        download_args = ["--browser", args.download_browser]
        if args.download_headless:
            download_args.append("--headless")
        run_module("surveyforge.src.stage1.step4_download", download_args)
        read_args = []
        if args.provider:
            read_args.extend(["--provider", args.provider])
        if args.providers:
            read_args.extend(["--providers", args.providers])
        if args.planning:
            read_args.extend(["--planning", args.planning])
        if args.enhanced_metrics:
            read_args.append("--enhanced-metrics")
        read_args.extend(["--max-workers", str(args.max_workers)])
        read_args.extend(["--papers-per-category", str(args.papers_per_category)])
        run_module("surveyforge.src.stage2.read_and_report", read_args)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
