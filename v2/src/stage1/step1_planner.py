import argparse
from pathlib import Path
from typing import Any, Dict

from ..core.io import ROOT, dump_json, load_json, load_yaml, stage_temp, write_text
from ..core.llm_client import LLMClient
from ..core.text import join_semicolon, normalize_string_list, normalize_space


def unique_terms(items):
    seen = set()
    result = []
    for item in normalize_string_list(items):
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def merge_defaults(base: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key, value in (previous or {}).items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def load_previous_input() -> Dict[str, Any]:
    path = ROOT / "temp/stage1/step1/input.json"
    if not path.exists():
        return {}
    try:
        data = load_json(path)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def prompt_value(label: str, default: str = "") -> str:
    print(f"{label}")
    if default:
        print(f"Current / 当前值: {default}")
    value = input("> ").strip()
    return value or default


def prompt_int(label: str, default: int) -> int:
    raw = prompt_value(label, str(default))
    return int(raw)


def prompt_list(label: str, default=None):
    default = default or []
    raw = prompt_value(f"{label} (comma separated / 使用英文逗号分隔)", ", ".join(default))
    return normalize_string_list(raw.split(","))


def collect_interactive_input(defaults: Dict[str, Any], provider: str) -> Dict[str, Any]:
    topic = prompt_value(
        "Research topic / 研究主题\n"
        "Use natural language to describe the survey goal. The LLM planner will convert it into broad Google Scholar search queries.\n"
        "请用自然语言描述综述目标，LLM 会据此生成覆盖面较全的 Google Scholar 检索式。\n"
        "Example / 示例：当前正在进行一项关于室内定位方案的文献综述，主题目标是 UWB 用于室内定位，请生成覆盖 ultra wideband/UWB 与 indoor positioning/localization 的检索式。",
        defaults.get("topic", ""),
    )
    year_from = prompt_int("Year from / 起始年份", int(defaults.get("year_range", {}).get("from", 2022)))
    year_to = prompt_int("Year to / 结束年份", int(defaults.get("year_range", {}).get("to", 2026)))
    gs_max_results = prompt_int(
        "Google Scholar max results for a single query / 单个检索式时的最大候选检索数",
        int(defaults.get("gs_max_results", 100)),
    )
    gs_min_results_per_query = prompt_int(
        "Google Scholar minimum results per query when multiple queries are generated / 多个检索式时每个检索式至少检索数量",
        int(defaults.get("gs_min_results_per_query", 30)),
    )
    target_keep_count = prompt_int("Target selected paper count / 目标精选文献数量", int(defaults.get("target_keep_count", 30)))
    venue_scope = prompt_list(
        "Venue scope / preferred and blocked venues / 期刊会议偏好与屏蔽范围",
        defaults.get("venue_scope", []),
    )
    topic_scope = prompt_list(
        "Topic scope / blocked or preferred topic terms / 主题范围、偏好或屏蔽词；可在默认值后手动补充具体关键词",
        defaults.get("topic_scope", []),
    )
    title_exclude_terms = prompt_list(
        "Title exclude terms / 标题屏蔽词；默认排除综述、趋势类标题，可手动补充",
        defaults.get("title_exclude_terms", []),
    )
    model_provider = provider or prompt_value("LLM provider / 大模型服务商", defaults.get("provider", "chatgpt"))
    return {
        "topic": topic,
        "year_range": {"from": year_from, "to": year_to},
        "gs_max_results": gs_max_results,
        "gs_min_results_per_query": gs_min_results_per_query,
        "target_keep_count": target_keep_count,
        "venue_scope": venue_scope,
        "topic_scope": topic_scope,
        "title_exclude_terms": title_exclude_terms,
        "provider": model_provider,
    }


def render_input_md(data: Dict[str, Any]) -> str:
    yr = data.get("year_range", {})
    return "\n".join(
        [
            "# Planning Input",
            "",
            f"- Topic: {data.get('topic', '')}",
            "- Search Query Rule: LLM planner converts natural-language topic into Google Scholar queries",
            f"- Year Range: {yr.get('from', '')} to {yr.get('to', '')}",
            f"- Google Scholar Max Results: {data.get('gs_max_results', '')}",
            f"- Google Scholar Min Results Per Query: {data.get('gs_min_results_per_query', '')}",
            f"- Target Keep Count: {data.get('target_keep_count', '')}",
            f"- Venue Scope: {join_semicolon(data.get('venue_scope', [])) or 'none'}",
            f"- Topic Scope: {join_semicolon(data.get('topic_scope', [])) or 'none'}",
            f"- Title Exclude Terms: {join_semicolon(data.get('title_exclude_terms', [])) or 'none'}",
            f"- Provider: {data.get('provider', '')}",
        ]
    )


def render_planning_md(plan: Dict[str, Any]) -> str:
    yr = plan.get("year_range", {})
    retrieval = plan.get("retrieval", {})
    return "\n".join(
        [
            "# Planning Output",
            "",
            f"- Topic: {plan.get('topic', '')}",
            "- Search Query Rule: generated Scholar queries are used for retrieval",
            f"- Year Range: {yr.get('from', '')} to {yr.get('to', '')}",
            f"- Google Scholar Max Results: {plan.get('gs_max_results', '')}",
            f"- Google Scholar Min Results Per Query: {plan.get('gs_min_results_per_query', '')}",
            f"- Target Keep Count: {plan.get('target_keep_count', '')}",
            f"- Search Query: {retrieval.get('search_query', '')}",
            f"- Search Queries: {join_semicolon(retrieval.get('search_queries', [])) or 'none'}",
            f"- Title Exclude Terms: {join_semicolon(retrieval.get('title_exclude_terms', [])) or 'none'}",
            f"- Venue Block Terms: {join_semicolon(retrieval.get('venue_block_terms', [])) or 'none'}",
            "",
            "## LLM Screening Prompt",
            "",
            normalize_space(plan.get("llm_screening_prompt", "")),
            "",
            "## Title Classification System Prompt",
            "",
            normalize_space(plan.get("title_classification_system_prompt", "")),
        ]
    )


def normalize_plan(plan: Dict[str, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
    retrieval = plan.get("retrieval") if isinstance(plan.get("retrieval"), dict) else {}
    policy = plan.get("screening_policy") if isinstance(plan.get("screening_policy"), dict) else {}
    title_exclude_terms = unique_terms(
        normalize_string_list(input_data.get("title_exclude_terms"))
        + normalize_string_list(retrieval.get("title_exclude_terms"))
    )
    search_queries = normalize_string_list(retrieval.get("search_queries"))
    if not search_queries:
        search_query = normalize_space(retrieval.get("search_query", ""))
        search_queries = [search_query] if search_query else [normalize_space(input_data["topic"])]
    search_queries = search_queries[:3]
    return {
        "topic": input_data["topic"],
        "year_range": input_data["year_range"],
        "gs_max_results": int(input_data["gs_max_results"]),
        "gs_min_results_per_query": int(input_data.get("gs_min_results_per_query", 30)),
        "target_keep_count": int(input_data["target_keep_count"]),
        "retrieval": {
            "search_query": search_queries[0],
            "search_queries": search_queries,
            "title_exclude_terms": title_exclude_terms,
            "venue_block_terms": normalize_string_list(retrieval.get("venue_block_terms")),
        },
        "screening_policy": policy,
        "llm_screening_prompt": normalize_space(plan.get("llm_screening_prompt", "")),
        "title_classification_system_prompt": normalize_space(
            plan.get("title_classification_system_prompt", "")
            or "You are an indoor positioning and navigation expert classifying paper titles for a high-quality survey."
        ),
        "notes": normalize_string_list(plan.get("notes")),
    }


def main():
    parser = argparse.ArgumentParser(description="Stage1 Step1: interactive planning with LLM.")
    parser.add_argument("--provider", default="")
    parser.add_argument("--no-interactive", action="store_true")
    parser.add_argument("--input", default="")
    parser.add_argument("--runtime", default=str(ROOT / "configs/runtime.yaml"))
    args = parser.parse_args()

    config_defaults = load_yaml(ROOT / "configs/defaults.yaml")
    previous_input = load_previous_input()
    defaults = merge_defaults(config_defaults, previous_input)
    out_dir = stage_temp("stage1", "step1")

    if args.input:
        input_data = load_yaml(Path(args.input))
    elif args.no_interactive:
        input_data = dict(defaults)
        if args.provider:
            input_data["provider"] = args.provider
    else:
        input_data = collect_interactive_input(defaults, args.provider)

    dump_json(out_dir / "input.json", input_data)
    write_text(out_dir / "input.md", render_input_md(input_data))

    client = LLMClient(provider=input_data.get("provider", args.provider or "auto"), runtime_path=Path(args.runtime))
    raw_plan = client.generate_json(ROOT / "prompts/planner.md", input_data)
    plan = normalize_plan(raw_plan if isinstance(raw_plan, dict) else {}, input_data)
    dump_json(out_dir / "planning.json", plan)
    write_text(out_dir / "planning.md", render_planning_md(plan))
    print(f"Planning saved to {out_dir / 'planning.json'}")


if __name__ == "__main__":
    main()
