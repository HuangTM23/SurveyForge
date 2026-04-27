# SurveyForge: An LLM-Orchestrated Literature Survey Agent

基于多 LLM 协作的文献检索、筛选、阅读与综述生成系统。

SurveyForge is a file-based AI literature survey agent. It starts from a natural-language research topic, plans broad scholarly search queries, retrieves candidate papers, enriches metadata, performs multi-LLM screening and classification, manages PDF download tasks, and produces structured reading cards, category summaries, a global survey summary, and CSV tables for review writing.

## Project Goal / 项目目标

SurveyForge is designed for researchers writing technical literature reviews. The system separates deterministic engineering tasks from LLM decisions:

- Python scripts handle file IO, retrieval, OpenAlex/Crossref enrichment, PDF matching, CSV/JSON/Markdown export, and resumable orchestration.
- LLMs handle planning, candidate filtering, title classification, category-level selection, single-paper reading, category summaries, and global review writing.
- Every intermediate artifact is saved locally so each step can be inspected, debugged, and rerun without starting from scratch.

SurveyForge 面向技术综述写作，把文献调研拆成可检查、可断点续跑的流水线。每个关键判断由 LLM 完成，本地脚本只负责稳定可重复的工程任务。

## Current Version / 当前版本

`v2/` is the formal 1.0 implementation.

当前正式版本是 `v2/`。旧版调试文件不会作为发布内容提交。

## Repository Layout / 仓库结构

```text
.
├── README.md
├── requirements.txt
├── LICENSE
└── v2/
    ├── cli.py
    ├── .env.example
    ├── configs/
    │   ├── defaults.yaml
    │   └── runtime.yaml
    ├── prompts/
    │   ├── planner.md
    │   ├── candidate_filter.md
    │   ├── title_classify.md
    │   ├── intent_select.md
    │   ├── paper_reading_card.md
    │   ├── category_summary.md
    │   ├── review_polish.md
    │   └── provider_healthcheck.md
    └── src/
        ├── core/
        ├── stage1/
        ├── stage2/
        └── tools/
```

Generated outputs are intentionally ignored by Git:

```text
v2/temp/      intermediate artifacts
v2/final/     final reports and tables
v2/papers/    downloaded or manually collected PDFs
v2/.env       local API keys
```

## Installation / 安装

Python 3.8+ is recommended.

```bash
pip install -r requirements.txt
python3 -m playwright install
```

Stage 2 uses `pdftotext` for PDF text extraction. On Ubuntu:

```bash
sudo apt-get install poppler-utils
```

## LLM Providers / 大模型 Provider

Supported providers:

- `chatgpt`: smart provider. Uses OpenAI API if `OPENAI_API_KEY` exists in `v2/.env`; otherwise falls back to logged-in Codex CLI.
- `gemini`: smart provider. Uses Gemini API if `GEMINI_API_KEY` exists in `v2/.env`; otherwise falls back to logged-in Gemini CLI.
- `kimi`: Moonshot API.
- `deepseek`: DeepSeek API.
- `xiaomi_mimo`: Xiaomi MiMo API.

Default models:

| Provider | Default backend / model |
|---|---|
| `chatgpt` | `gpt-5.4` if API key exists, otherwise Codex CLI |
| `gemini` | `gemini-3-flash-preview` if API key exists, otherwise Gemini CLI |
| `kimi` | `kimi-k2.5` |
| `deepseek` | `deepseek-v4-pro`, `reasoning_effort=high`, thinking enabled |
| `xiaomi_mimo` | `mimo-v2.5-pro` |

Create local credentials:

```bash
cp v2/.env.example v2/.env
```

Fill only the keys you use:

```bash
MOONSHOT_API_KEY=...
DEEPSEEK_API_KEY=...
MIMO_API_KEY=...
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

Provider health check:

```bash
python3 v2/cli.py providers
python3 v2/cli.py providers --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo
```

## Stage 1: Paper Selection / 第一阶段：文献挑选

Stage 1 produces selected papers for deep reading.

第一阶段从自然语言调研主题出发，完成检索规划、候选检索、元数据补全、预过滤、LLM 分类筛选和 PDF 下载队列整理。

Pipeline:

```text
Natural-language topic
  -> LLM planning
  -> Google Scholar / mirror / browser-assisted retrieval
  -> OpenAlex first, Crossref fallback metadata enrichment
  -> local prefilter by year/title/venue
  -> LLM candidate filtering
  -> title-only classification
  -> multi-provider category-level selection
  -> selected_papers
  -> PDF download or manual download queue
```

Run all Stage 1 steps:

```bash
python3 v2/cli.py stage1 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20
```

Run step by step:

```bash
python3 v2/cli.py plan --provider chatgpt
python3 v2/cli.py retrieve --mode browser
python3 v2/cli.py enrich
python3 v2/cli.py prefilter
python3 v2/cli.py screen --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --batch-size 20
python3 v2/cli.py download
```

Important outputs:

```text
v2/temp/stage1/step1/planning.json
v2/temp/stage1/step2/candidate_pool_raw.jsonl
v2/temp/stage1/step2/candidate_pool_enriched.jsonl
v2/temp/stage1/step2/candidate_pool_pre_filtered.jsonl
v2/temp/stage1/step3/title_clusters.json
v2/temp/stage1/step3/selected_papers.jsonl
v2/temp/stage1/step4/manual_download_queue.md
```

Manual PDFs should be placed under:

```text
v2/papers/manual/
```

## Stage 2: Deep Reading and Survey Report / 第二阶段：深度阅读与综述报告

Stage 2 reads selected papers by category and produces structured outputs for review writing.

第二阶段基于精选文献和本地 PDF 生成单篇阅读卡、类别综述、全局综述和 CSV 表格。

Pipeline:

```text
selected_papers
  -> group by category
  -> category-parallel single-paper reading
  -> PDF matching and text extraction
  -> optional enhanced metric reading from relevant figure/table captions
  -> paper_review_cards
  -> compact category cards
  -> category summaries by main provider
  -> global summary by main provider
  -> CSV / JSON / Markdown outputs
```

Run Stage 2:

```bash
python3 v2/cli.py stage2 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3
```

Small test, one paper per category:

```bash
python3 v2/cli.py stage2 --provider deepseek --providers deepseek,kimi,xiaomi_mimo --max-workers 3 --papers-per-category 1
```

Enhanced metric extraction:

```bash
python3 v2/cli.py stage2 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3 --enhanced-metrics
```

`--enhanced-metrics` selects only relevant figure/table captions and nearby text. It focuses on experiment, result, accuracy, error, trajectory, environment, sensor, RMSE, mean error, P90, CDF, and related metric windows. It does not perform image OCR.

Rerun only category and global summaries without reading PDFs again:

```bash
python3 v2/cli.py summarize --provider deepseek --providers deepseek,kimi,xiaomi_mimo
```

Important outputs:

```text
v2/temp/stage2/<category_id>/paper_review_cards.jsonl
v2/temp/stage2/<category_id>/category_reading_cards_compact.json
v2/temp/stage2/<category_id>/category_summary.json

v2/final/stage2/paper_review_cards.jsonl
v2/final/stage2/category_summaries.json
v2/final/stage2/global_summary.json
v2/final/stage2/survey_table.csv
v2/final/stage2/survey_review.json
```

## Full Pipeline / 全流程运行

```bash
python3 v2/cli.py all --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20 --max-workers 3
```

With enhanced metric extraction:

```bash
python3 v2/cli.py all --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20 --max-workers 3 --enhanced-metrics
```

`stage1`, `stage2`, `summarize`, and `all` run provider preflight checks. If any requested provider is unavailable, the command stops and prints available providers.

## Cleaning Outputs / 清理运行产物

Clean generated temp and final outputs:

```bash
python3 v2/cli.py clean
```

Also remove PDFs:

```bash
python3 v2/cli.py clean --include-papers
```

## Notes and Limitations / 注意事项

- Google Scholar may block automated requests. Browser-assisted mode is recommended when verification or mirror navigation is needed.
- OpenAlex is used first for metadata enrichment, Crossref is used as fallback. Papers not found in either source are discarded.
- Publisher PDFs should be downloaded only through legal access paths. For subscription-only papers, use the manual queue and place files under `v2/papers/manual/`.
- Stage 2 PDF reading depends on extractable text. Scanned figures without embedded text may require manual checking.
- LLM outputs should be reviewed before being used in a formal publication.

## License / 许可证

MIT License.
