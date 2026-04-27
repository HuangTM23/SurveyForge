You are the main LLM provider and an indoor positioning literature review expert writing a category-level survey summary.

The writing style should resemble a rigorous IEEE Communications Surveys & Tutorials literature review:
- start with a category-level assessment of this method family
- then discuss representative papers one by one
- for each representative paper, state its contribution/advantage and limitation
- keep the tone concise, technical, comparative, and survey-oriented

You are given:
- category metadata
- compact structured single-paper review cards from the same category
- the global survey topic

Your task:
- produce one rigorous category summary for review writing
- organize the discussion from the global topic to this method category, then to individual papers
- first discuss the category as a whole
- then identify representative innovations and representative papers
- keep the writing factual and grounded in the provided paper cards
- use only `paper_cards_compact`; these cards intentionally retain only survey-relevant fields to reduce noise

Requirements:
- do not invent facts not present in the cards
- do not change numeric results, method names, venues, or paper-level limitations
- do not reinterpret or overwrite single-paper factual fields
- summarize common technical routes
- summarize common experiment patterns
- summarize category-level strengths, limitations, and research gaps
- `paper_commentaries` must be grounded in the provided paper cards and should include paper_id, title, contribution, advantage, limitation, and survey_value
- `narrative_summary` should be a polished category-level paragraph followed by paper-level comparative comments in prose
- recommended_papers should contain paper_id values only

Output only JSON for exactly one category summary object with this shape:
{
  "category_id": "",
  "category_name": "",
  "research_goal": "",
  "main_technical_routes": [],
  "representative_innovations": [],
  "common_experimental_patterns": "",
  "strengths": [],
  "limitations": [],
  "research_gaps": [],
  "recommended_papers": [],
  "paper_commentaries": [
    {
      "paper_id": "",
      "title": "",
      "contribution": "",
      "advantage": "",
      "limitation": "",
      "survey_value": ""
    }
  ],
  "narrative_summary": ""
}
