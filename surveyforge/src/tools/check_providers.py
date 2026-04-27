import argparse
import json
from typing import List

from ..core.llm_client import check_provider
from ..core.text import normalize_space


DEFAULT_PROVIDERS = ["chatgpt", "gemini", "kimi", "deepseek", "xiaomi_mimo"]


def parse_providers(raw: str) -> List[str]:
    providers = [normalize_space(item) for item in raw.split(",") if normalize_space(item)]
    return providers or DEFAULT_PROVIDERS


def main() -> None:
    parser = argparse.ArgumentParser(description="Check configured LLM providers.")
    parser.add_argument("--providers", default=",".join(DEFAULT_PROVIDERS))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = []
    for provider in parse_providers(args.providers):
        ok, message = check_provider(provider, timeout_seconds=args.timeout)
        row = {"provider": provider, "ok": ok, "message": message}
        results.append(row)
        if not args.json:
            status = "OK" if ok else "FAILED"
            print(f"[{status}] {provider}: {message}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
