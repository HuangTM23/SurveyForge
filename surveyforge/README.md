# SurveyForge: An LLM-Orchestrated Literature Survey Agent

SurveyForge 的目标是构建一个面向高质量综述写作的 AI Agent：从自然语言调研主题出发，自动规划检索式、获取候选文献、补全可靠元数据、进行 LLM 筛选与分类、下载或整理全文，并最终围绕每篇文献和每个技术类别生成可直接用于综述写作的结构化材料。

设计思路：

- 把“文献调研”拆成可检查、可断点续跑的文件化流水线。
- 每个关键决策由 LLM 完成，例如检索式规划、候选过滤、标题分类、类内精选、单篇阅读、类别总结和全局综述。
- 本地 Python 脚本只负责稳定、可重复的工程任务，例如文件读写、Scholar 抓取、OpenAlex/Crossref 补全、PDF 管理、CSV/JSON/Markdown 导出。
- 单篇阅读只做事实抽取，类别总结和全局总结再做综述式组织，避免把事实抽取和综述写作混在一个提示词里。
- 所有中间结果都落盘到 `surveyforge/temp`，最终结果落盘到 `surveyforge/final`，便于人工检查和重新运行局部步骤。

核心目标：

- Stage 1：从自然语言调研主题出发，检索、补全、过滤、分类并精选文献。
- Stage 2：对精选文献做深度阅读，生成单篇阅读卡片、类别综述、全局综述和 CSV 表格。

## 1. 文件结构

```text
surveyforge/
  cli.py
    统一命令行入口。

  configs/
    defaults.yaml
      交互默认值，包括年份范围、检索数量、期刊偏好、主题屏蔽词等。
    runtime.yaml
      LLM 运行配置，默认 provider、超时、重试、温度等。

  prompts/
    planner.md
      Stage 1 Step 1：把自然语言调研目标转换成检索式、筛选规则和提示词。
    candidate_filter.md
      Stage 1 Step 3：候选池第一轮 LLM 过滤。
    title_classify.md
      Stage 1 Step 3：只基于标题做方法类别分类。
    intent_select.md
      Stage 1 Step 3：每个类别内部多 provider 精选文献。
    paper_reading_card.md
      Stage 2：单篇文献深度阅读事实抽取。
    category_summary.md
      Stage 2：类别级综述总结。
    review_polish.md
      Stage 2：全局综述总结和风格统一。
    provider_healthcheck.md
      LLM provider 可用性测试。

  src/
    core/
      io.py
        JSON、JSONL、CSV、Markdown、YAML、本地目录读写工具。
      llm_client.py
        API provider 和 CLI provider 的统一 LLM 调用层。
      text.py
        文本清洗、列表归一化、slug 等工具。

    stage1/
      step1_planner.py
        交互式规划。输入自然语言主题，调用 LLM 生成 planning.json。
      step2_retrieve.py
        Google Scholar / 镜像 / 浏览器辅助检索，只抓取候选标题列表。
      step2_enrich.py
        用 OpenAlex 优先、Crossref fallback 补全 DOI、期刊、作者、摘要、引用量等元数据。
      step2_prefilter.py
        基于年份、标题屏蔽词、期刊屏蔽词、本地规则做预过滤。
      step3_screen.py
        LLM 候选过滤、标题分类、多 provider 类内精选和投票合并。
      step4_download.py
        PDF 下载与手动下载清单生成。IEEE/MDPI 尝试自动下载，Elsevier 等输出链接或 DOI。

    stage2/
      read_and_report.py
        按类别并行阅读论文，生成单篇阅读卡片、类别总结、全局总结和 CSV。

    tools/
      check_providers.py
        测试 LLM provider 是否可用。
      clean_outputs.py
        清理 temp/final，便于切换 topic 后重新运行。

  temp/
    中间产物目录。由程序生成，可用 clean 清理。

  papers/
    PDF 存放目录。默认 clean 不删除。

  final/
    最终结果目录。由程序生成，可用 clean 清理。
```

## 2. Provider 机制

支持两类 LLM provider：

- 智能 provider：`chatgpt`、`gemini`
- API key 直连 provider：`kimi`、`deepseek`、`xiaomi_mimo`

命名约定：

- `chatgpt`：智能 provider。若 `.env` 中存在 `OPENAI_API_KEY`，优先走 OpenAI API；否则回退到已登录的 Codex CLI。
- `gemini`：智能 provider。若 `.env` 中存在 `GEMINI_API_KEY`，优先走 Gemini API；否则回退到已登录的 Gemini CLI。

默认主 provider 是 `chatgpt`。因此如果配置了 `OPENAI_API_KEY`，默认主 provider 会走 OpenAI API；如果没有配置，则走 Codex CLI。

API key 放在项目根目录 `.env`：

```bash
MOONSHOT_API_KEY=...
DEEPSEEK_API_KEY=...
MIMO_API_KEY=...
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

默认 provider / model：

| Provider | Type | Default model / backend | Notes |
|---|---|---|---|
| `chatgpt` | Smart | `gpt-5.4` if `OPENAI_API_KEY` exists, otherwise `codex` CLI | 默认主 provider。 |
| `gemini` | Smart | `gemini-3-flash-preview` if `GEMINI_API_KEY` exists, otherwise `gemini` CLI | API key 优先，CLI 兜底。 |
| `kimi` | API | `kimi-k2.5` | 使用 `MOONSHOT_API_KEY`。 |
| `deepseek` | API | `deepseek-v4-pro` | 使用 `DEEPSEEK_API_KEY`，默认 `reasoning_effort=high` 且 thinking enabled。 |
| `xiaomi_mimo` | API | `mimo-v2.5-pro` | 使用 `MIMO_API_KEY`。 |

测试 provider：

```bash
python3 surveyforge/cli.py providers
```

指定 provider：

```bash
python3 surveyforge/cli.py providers --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo
```

## 3. Stage 1 架构：文献挑选

Stage 1 目标产物：

```text
surveyforge/temp/stage1/step3/selected_papers.jsonl
surveyforge/temp/stage1/step3/selected_papers.md
```

流程：

```text
自然语言调研主题
  -> LLM Planner 生成检索式和筛选规则
  -> Google Scholar / 镜像检索标题列表
  -> OpenAlex/Crossref 补全可靠元数据
  -> 本地预过滤
  -> LLM 候选过滤
  -> LLM 标题分类
  -> 多 provider 类内精选
  -> selected_papers
  -> PDF 下载或手动下载清单
```

### Step 1: Planning

命令：

```bash
python3 surveyforge/cli.py plan --provider chatgpt
```

输入：

- 自然语言研究主题
- 年份范围
- 单检索式最大检索数量
- 多检索式时每个检索式最小检索数量
- 目标精选文献数量
- 期刊/会议偏好和屏蔽
- 主题偏好和屏蔽
- 标题屏蔽词

输出：

```text
surveyforge/temp/stage1/step1/input.json
surveyforge/temp/stage1/step1/input.md
surveyforge/temp/stage1/step1/planning.json
surveyforge/temp/stage1/step1/planning.md
```

关键字段：

- `topic`：用户自然语言调研目标。
- `retrieval.search_queries`：LLM 生成的 Google Scholar 检索式列表。
- `retrieval.search_queries` 最多保留 3 条，避免检索轮次过多。
- `gs_max_results`：只有一个检索式时的最大抓取数量。
- `gs_min_results_per_query`：多个检索式时每个检索式的抓取数量。
- `target_keep_count`：最终希望进入深度阅读的文献数量。

### Step 2: Retrieve

命令：

```bash
python3 surveyforge/cli.py retrieve --mode browser
```

任务：

- 读取 `planning.json` 中的 `retrieval.search_queries`。
- 对每个检索式分别进行 Scholar 检索。
- 使用年份范围限制搜索。
- 只信任 Scholar 标题列表，不信任其元数据。
- 多个检索式结果会合并并按标题去重。

输出：

```text
surveyforge/temp/stage1/step2/retrieval_input.json
surveyforge/temp/stage1/step2/retrieval_status.json
surveyforge/temp/stage1/step2/candidate_pool_raw.jsonl
surveyforge/temp/stage1/step2/candidate_pool_raw.md
```

### Step 3: Enrich

命令：

```bash
python3 surveyforge/cli.py enrich
```

任务：

- 读取 Scholar 标题列表。
- 优先通过标题查询 OpenAlex。
- OpenAlex 找不到时 fallback 到 Crossref。
- 两者都找不到则丢弃。
- 用 OpenAlex/Crossref 数据覆盖 Scholar 不可靠元数据。

补全字段：

- title
- doi
- venue
- publisher
- year
- publication_date
- citation_count
- abstract
- authors
- first_author
- first_affiliation
- pdf_url
- publication_type

输出：

```text
surveyforge/temp/stage1/step2/candidate_pool_enriched.jsonl
surveyforge/temp/stage1/step2/candidate_pool_enriched.md
surveyforge/temp/stage1/step2/candidate_pool_enrichment_rejected.jsonl
surveyforge/temp/stage1/step2/candidate_pool_enrichment_rejected.md
surveyforge/temp/stage1/step2/enrichment_summary.json
```

### Step 4: Prefilter

命令：

```bash
python3 surveyforge/cli.py prefilter
```

任务：

- 按年份范围过滤。
- 按标题屏蔽词过滤。
- 按期刊/会议屏蔽词过滤。
- 使用 `publication_type` 辅助识别会议、期刊、预印本等类型。

输出：

```text
surveyforge/temp/stage1/step2/candidate_pool_pre_filtered.jsonl
surveyforge/temp/stage1/step2/candidate_pool_pre_filtered.md
surveyforge/temp/stage1/step2/candidate_pool_rejected.jsonl
surveyforge/temp/stage1/step2/candidate_pool_rejected.md
surveyforge/temp/stage1/step2/prefilter_summary.json
```

### Step 5: Screen

命令：

```bash
python3 surveyforge/cli.py screen --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --batch-size 20
```

任务：

- Candidate Pool LLM 过滤。
- 只基于标题做方法类别分类。
- 每个类别内部使用多个 provider 独立精选。
- 按 provider 投票结果合并最终 selected papers。

输出：

```text
surveyforge/temp/stage1/step3/candidate_pool_filtered.jsonl
surveyforge/temp/stage1/step3/candidate_pool_filtered.md
surveyforge/temp/stage1/step3/title_clusters.json
surveyforge/temp/stage1/step3/categories/*.jsonl
surveyforge/temp/stage1/step3/categories/*.md
surveyforge/temp/stage1/step3/selection_votes.jsonl
surveyforge/temp/stage1/step3/selected_papers.jsonl
surveyforge/temp/stage1/step3/selected_papers.md
```

### Step 6: Download

命令：

```bash
python3 surveyforge/cli.py download
```

任务：

- 读取 `selected_papers.jsonl`。
- IEEE/MDPI 尝试自动下载 PDF。
- Elsevier 和其他来源输出 PDF 链接或 DOI。
- 失败文献进入手动下载清单，并在 `surveyforge/papers/manual/` 生成手动下载 PDF 的存放目录。
- 手动下载时按 `manual_download_queue.md` 里的 `Manual save path` 保存文件名，Stage 2 会递归扫描 `surveyforge/papers/`，因此会自动识别 `manual/` 下的 PDF。

输出：

```text
surveyforge/papers/
surveyforge/papers/manual/
surveyforge/temp/stage1/step4/download_results.jsonl
surveyforge/temp/stage1/step4/download_results.md
surveyforge/temp/stage1/step4/manual_download_queue.jsonl
surveyforge/temp/stage1/step4/manual_download_queue.md
surveyforge/temp/stage1/step4/download_summary.json
```

## 4. Stage 2 架构：深度阅读与综述

Stage 2 输入：

```text
surveyforge/temp/stage1/step3/selected_papers.jsonl
surveyforge/papers/
```

Stage 2 流程：

```text
selected_papers
  -> 按 category_id 分组
  -> 每个类别并行阅读单篇论文
  -> 每篇论文匹配本地 PDF
  -> PDF 转文本：保留前 2 页 + Abstract 到 Conclusion
  -> 可选增强阅读：额外抽取图题、表题、表格文本和指标关键词窗口
  -> LLM 生成单篇阅读卡片
  -> 每个类别生成 compact 阅读卡文件
  -> 主 provider 基于 compact 阅读卡生成类别总结
  -> 主 provider 基于类别总结为主、compact 阅读卡为辅生成全局总结
  -> 输出 CSV / JSON / Markdown
```

单篇阅读字段：

- first_author
- first_affiliation
- research_problem
- research_method
- method_type
- innovation
- experiment_type
- experiment_metric
- experiment_environment
- limitations

命令：

```bash
python3 surveyforge/cli.py stage2 --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3
```

增强阅读模式，用于更完整提取图表中的 `Experiment Metric`：

```bash
python3 surveyforge/cli.py stage2 --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3 --enhanced-metrics
```

说明：

- 默认模式更快，适合批量初读。
- `--enhanced-metrics` 会先按 figure/table caption 的相关性筛选图表，只保留实验、结果、误差、精度、轨迹、环境、传感器等相关图表附近文本，并加入 RMSE/mean error/P90/CDF/accuracy 等指标窗口。
- 该模式不做图片 OCR；如果指标只存在于不可复制的图片中，仍可能需要人工核对。

小批量测试，每个类别只读 1 篇：

```bash
python3 surveyforge/cli.py stage2 --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3 --papers-per-category 1
```

只重跑类别总结和全局总结，不重新阅读 PDF：

```bash
python3 surveyforge/cli.py summarize --provider deepseek --providers deepseek,kimi,xiaomi_mimo
```

该命令读取已有：

```text
surveyforge/temp/stage2/<category_id>/paper_review_cards.jsonl
surveyforge/temp/stage2/<category_id>/category_reading_cards_compact.json
```

然后重新生成：

```text
surveyforge/temp/stage2/<category_id>/category_summary.json
surveyforge/temp/stage2/<category_id>/category_summary.md
surveyforge/final/stage2/category_summaries.json
surveyforge/final/stage2/global_summary.json
surveyforge/final/stage2/survey_review.json
```

输出：

```text
surveyforge/temp/stage2/<category_id>/paper_review_cards.jsonl
surveyforge/temp/stage2/<category_id>/paper_review_cards.md
surveyforge/temp/stage2/<category_id>/category_reading_cards_compact.json
surveyforge/temp/stage2/<category_id>/category_reading_cards_compact.md
surveyforge/temp/stage2/<category_id>/category_summary.json
surveyforge/temp/stage2/<category_id>/category_summary.md

surveyforge/final/stage2/paper_review_cards.json
surveyforge/final/stage2/paper_review_cards.jsonl
surveyforge/final/stage2/paper_review_cards.md
surveyforge/final/stage2/category_summaries.json
surveyforge/final/stage2/category_summaries.md
surveyforge/final/stage2/global_summary.json
surveyforge/final/stage2/global_summary.md
surveyforge/final/stage2/survey_table.csv
surveyforge/final/stage2/survey_review.json
```

## 5. 一键运行

`stage1`、`stage2` 和 `all` 属于一键运行命令。执行前会自动测试传入的 LLM provider：

- 所有 provider 可用：继续运行。
- 任意 provider 不可用：立即暂停，不执行后续阶段，并打印可用 provider 列表。
- 用户需要根据提示手动重新选择可用 provider 后再运行。

Stage 1 一键运行：

```bash
python3 surveyforge/cli.py stage1 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20
```

Stage 2 一键运行：

```bash
python3 surveyforge/cli.py stage2 --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3
```

Stage 2 增强图表指标阅读：

```bash
python3 surveyforge/cli.py stage2 --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3 --enhanced-metrics
```

全流程一键运行：

```bash
python3 surveyforge/cli.py all --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20 --max-workers 3
```

全流程并在 Stage 2 启用增强指标阅读：

```bash
python3 surveyforge/cli.py all --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20 --max-workers 3 --enhanced-metrics
```

## 6. 清理与切换 Topic

切换 topic 前清空生成结果：

```bash
python3 surveyforge/cli.py clean
```

清理范围：

```text
surveyforge/temp
surveyforge/final
```

默认不会删除 PDF。

如果确认要删除已下载 PDF：

```bash
python3 surveyforge/cli.py clean --include-papers
```

## 7. 常用检查点

规划结果：

```text
surveyforge/temp/stage1/step1/planning.md
```

Scholar 原始标题池：

```text
surveyforge/temp/stage1/step2/candidate_pool_raw.md
```

OpenAlex/Crossref 补全后候选池：

```text
surveyforge/temp/stage1/step2/candidate_pool_enriched.md
```

预过滤后候选池：

```text
surveyforge/temp/stage1/step2/candidate_pool_pre_filtered.md
```

最终精选文献：

```text
surveyforge/temp/stage1/step3/selected_papers.md
```

最终 CSV：

```text
surveyforge/final/stage2/survey_table.csv
```

单篇阅读卡片：

```text
surveyforge/final/stage2/paper_review_cards.md
```

类别综述：

```text
surveyforge/final/stage2/category_summaries.md
```

全局综述：

```text
surveyforge/final/stage2/global_summary.md
```

## 8. 常见问题

### Google Scholar 403 / 429

使用浏览器辅助模式：

```bash
python3 surveyforge/cli.py retrieve --mode browser
```

在浏览器中处理验证码、登录或跳转到可用镜像后，回到终端按回车继续。

### Provider 不可用

先测试：

```bash
python3 surveyforge/cli.py providers --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo
```

如果 API provider 报 key 错误，检查项目根目录 `.env`。

如果 CLI provider 报认证错误，先完成对应 CLI 登录。

### Stage 2 阅读效果不佳

优先检查：

- `surveyforge/papers/` 是否有对应 PDF。
- `paper_review_cards.md` 中 `Reading Source` 是否为 `pdf / fulltext`。
- PDF 是否能被 `pdftotext` 正确解析。
- `Experiment Metric` 是否来自正文实验章节，而不是摘要泛化描述。
