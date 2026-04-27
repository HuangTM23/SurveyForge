# SurveyForge：基于多 LLM 协作的文献检索、筛选、阅读与综述生成系统

SurveyForge 是一个面向技术综述写作的 AI Agent。它从自然语言调研主题出发，自动规划检索式、抓取候选文献、补全元数据、进行多 LLM 筛选与分类、整理 PDF 下载任务，并最终生成单篇阅读卡片、类别综述、全局综述和 CSV 表格。

<span style="color:#d73a49"><b>重要说明：</b></span>SurveyForge 不是完全无人值守的爬虫系统。它的设计目标是把 LLM 决策与本地工程脚本结合起来，在关键节点保留人工确认能力，避免错误检索、非法下载、低质量元数据和不可控的自动化行为。

## 1. 项目目标

SurveyForge 适合用于高质量学术综述或技术调研，核心思想是：

- LLM 负责智能决策：检索规划、候选过滤、标题分类、类内精选、单篇阅读、类别总结和全局综述。
- Python 脚本负责稳定工程任务：文件读写、Google Scholar 检索、OpenAlex/Crossref 元数据补全、PDF 匹配、CSV/JSON/Markdown 输出。
- 所有中间结果都保存到本地文件，方便检查、调试、断点续跑。
- 单篇阅读只做事实抽取，类别总结和全局总结再做综述式组织，避免事实抽取和综述写作混在一个提示词里。

当前正式版本是：

```text
v2/
```

## 2. 项目结构

```text
.
├── README.md
├── README_CN.md
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

运行产物默认不提交到 Git：

```text
v2/temp/      中间文件
v2/final/     最终结果
v2/papers/    自动或手动下载的 PDF
v2/.env       本地 API key
```

## 3. 安装

建议使用 Python 3.8+。

```bash
pip install -r requirements.txt
python3 -m playwright install
```

Stage 2 需要 `pdftotext` 解析 PDF 文本。Ubuntu 可安装：

```bash
sudo apt-get install poppler-utils
```

## 4. LLM Provider 配置

支持的 provider：

- `chatgpt`：智能 provider。如果 `v2/.env` 中存在 `OPENAI_API_KEY`，优先走 OpenAI API；否则回退到已登录的 Codex CLI。
- `gemini`：智能 provider。如果 `v2/.env` 中存在 `GEMINI_API_KEY`，优先走 Gemini API；否则回退到已登录的 Gemini CLI。
- `kimi`：Moonshot API。
- `deepseek`：DeepSeek API。
- `xiaomi_mimo`：小米 MiMo API。

默认模型：

| Provider | 默认后端 / 模型 |
|---|---|
| `chatgpt` | 有 `OPENAI_API_KEY` 时使用 `gpt-5.4`，否则使用 Codex CLI |
| `gemini` | 有 `GEMINI_API_KEY` 时使用 `gemini-3-flash-preview`，否则使用 Gemini CLI |
| `kimi` | `kimi-k2.5` |
| `deepseek` | `deepseek-v4-pro`，默认 `reasoning_effort=high` 且开启 thinking |
| `xiaomi_mimo` | `mimo-v2.5-pro` |

创建本地密钥文件：

```bash
cp v2/.env.example v2/.env
```

只填写你实际使用的 key：

```bash
MOONSHOT_API_KEY=...
DEEPSEEK_API_KEY=...
MIMO_API_KEY=...
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

测试 provider：

```bash
python3 v2/cli.py providers
python3 v2/cli.py providers --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo
```

<span style="color:#0969da"><b>人工干预节点 A：Provider 可用性确认。</b></span>如果某个 provider 不可用，`stage1`、`stage2`、`summarize`、`all` 会暂停运行，并提示你重新选择可用 provider。

## 5. Stage 1：文献挑选

Stage 1 目标是生成精选文献列表：

```text
v2/temp/stage1/step3/selected_papers.jsonl
v2/temp/stage1/step3/selected_papers.md
```

流程：

```text
自然语言调研主题
  -> LLM Planner 生成检索式和筛选规则
  -> Google Scholar / 镜像 / 浏览器辅助检索
  -> OpenAlex 优先、Crossref fallback 元数据补全
  -> 本地预过滤
  -> LLM 候选过滤
  -> LLM 标题分类
  -> 多 provider 类内精选
  -> selected_papers
  -> PDF 自动下载或手动下载清单
```

一键运行 Stage 1：

```bash
python3 v2/cli.py stage1 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20
```

分步运行：

```bash
python3 v2/cli.py plan --provider chatgpt
python3 v2/cli.py retrieve --mode browser
python3 v2/cli.py enrich
python3 v2/cli.py prefilter
python3 v2/cli.py screen --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --batch-size 20
python3 v2/cli.py download
```

关键输出：

```text
v2/temp/stage1/step1/planning.json
v2/temp/stage1/step2/candidate_pool_raw.jsonl
v2/temp/stage1/step2/candidate_pool_enriched.jsonl
v2/temp/stage1/step2/candidate_pool_pre_filtered.jsonl
v2/temp/stage1/step3/title_clusters.json
v2/temp/stage1/step3/selected_papers.jsonl
v2/temp/stage1/step4/manual_download_queue.md
```

### Stage 1 中需要人工关注的节点

<span style="color:#d97706"><b>人工干预节点 B：Google Scholar 检索。</b></span>

Google Scholar 可能出现验证码、429、镜像跳转或空结果。推荐使用：

```bash
python3 v2/cli.py retrieve --mode browser
```

浏览器模式会给你处理验证码、登录或镜像跳转的机会。处理完成后再继续自动翻页抓取标题列表。

<span style="color:#d97706"><b>人工干预节点 C：检查检索和元数据补全结果。</b></span>

建议检查：

```text
v2/temp/stage1/step2/candidate_pool_raw.md
v2/temp/stage1/step2/candidate_pool_enriched.md
v2/temp/stage1/step2/candidate_pool_pre_filtered.md
```

OpenAlex 会优先补全 DOI、期刊、年份、作者、摘要、引用量；Crossref 作为 fallback。如果两个数据源都找不到，该文献会被丢弃。

<span style="color:#d73a49"><b>人工干预节点 D：文献下载。</b></span>

PDF 下载是最需要人工确认的环节：

- IEEE / MDPI：系统会尝试自动下载。
- Elsevier / ScienceDirect：系统通常只提供 PDF 链接或 DOI，需要你通过机构权限手动下载。
- 其他出版商：系统会输出 DOI / URL，通常需要手动下载。

手动下载清单：

```text
v2/temp/stage1/step4/manual_download_queue.md
v2/temp/stage1/step4/manual_download_queue.jsonl
```

手动下载 PDF 后，请放到：

```text
v2/papers/manual/
```

<span style="color:#2da44e"><b>重要：不是所有文献都必须下载 PDF。</b></span>

如果某些文献没有 PDF，Stage 2 仍然可以基于以下信息让 LLM 做保守阅读：

- 标题
- 摘要
- DOI
- URL
- 期刊
- 年份
- 引用量
- 分类和筛选理由

这种情况下，阅读卡会标记为 `metadata_only` 或 `weak_inference`，适合初步综述分析，但如果要写入正式论文，建议人工核对原文。

## 6. Stage 2：深度阅读与综述报告

Stage 2 基于精选文献和本地 PDF 生成：

- 单篇阅读卡片
- 类别 compact 阅读卡
- 类别总结
- 全局总结
- CSV 表格

流程：

```text
selected_papers
  -> 按 category_id 分组
  -> 每个类别并行阅读单篇论文
  -> 匹配本地 PDF
  -> PDF 转文本
  -> 可选增强指标阅读
  -> 生成 paper_review_cards
  -> 生成 category_reading_cards_compact
  -> 主 provider 生成类别总结
  -> 主 provider 生成全局总结
  -> 输出 CSV / JSON / Markdown
```

运行 Stage 2：

```bash
python3 v2/cli.py stage2 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3
```

小批量测试，每个类别只读 1 篇：

```bash
python3 v2/cli.py stage2 --provider deepseek --providers deepseek,kimi,xiaomi_mimo --max-workers 3 --papers-per-category 1
```

增强指标阅读：

```bash
python3 v2/cli.py stage2 --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --max-workers 3 --enhanced-metrics
```

`--enhanced-metrics` 会先按图题/表题相关性筛选图表，只保留实验、结果、误差、精度、轨迹、环境、传感器等相关图表附近文本，并加入 RMSE、mean error、P90、CDF、accuracy 等指标窗口。

<span style="color:#d97706"><b>人工干预节点 E：实验指标核对。</b></span>

如果指标只存在于图片中，且 PDF 文本无法提取，系统不会做 OCR。此时建议人工查看原文图表，并在最终使用前核对：

- 定位精度
- RMSE / mean error / median error / P90 / CDF
- 测距误差
- NLOS 分类准确率
- 实验场地尺寸
- 轨迹长度
- 传感器、锚点、标签、LED、相机等实验配置

只重跑类别总结和全局总结，不重新阅读 PDF：

```bash
python3 v2/cli.py summarize --provider deepseek --providers deepseek,kimi,xiaomi_mimo
```

该命令读取已有：

```text
v2/temp/stage2/<category_id>/paper_review_cards.jsonl
v2/temp/stage2/<category_id>/category_reading_cards_compact.json
```

然后重新生成：

```text
v2/temp/stage2/<category_id>/category_summary.json
v2/final/stage2/category_summaries.json
v2/final/stage2/global_summary.json
v2/final/stage2/survey_review.json
```

## 7. 全流程运行

```bash
python3 v2/cli.py all --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20 --max-workers 3
```

开启增强指标阅读：

```bash
python3 v2/cli.py all --provider chatgpt --providers chatgpt,gemini,kimi,deepseek,xiaomi_mimo --retrieve-mode browser --batch-size 20 --max-workers 3 --enhanced-metrics
```

## 8. 清理输出

只清理 `temp` 和 `final`：

```bash
python3 v2/cli.py clean
```

同时删除 PDF：

```bash
python3 v2/cli.py clean --include-papers
```

## 9. 输出文件

最终结果位于：

```text
v2/final/stage2/
```

主要文件：

```text
paper_review_cards.jsonl
paper_review_cards.md
category_summaries.json
category_summaries.md
global_summary.json
global_summary.md
survey_table.csv
survey_review.json
```

## 10. 使用建议

- 先用 `--papers-per-category 1` 小批量测试提示词和 provider。
- 检索结果质量不好时，先检查 `planning.json` 中的检索式。
- 元数据缺失时，检查 OpenAlex/Crossref 是否能根据标题找到论文。
- PDF 下载失败时，优先查看 `manual_download_queue.md`。
- 不要把 LLM 输出直接当作最终论文内容，正式写作前必须人工核对关键事实和数值。

## 11. 许可证

MIT License.
