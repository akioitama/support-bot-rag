# RAG grounding report (Ollama docs corpus)

65 questions in `eval/qa_grounding.json`, written from the live uploaded file `uploads/2_ollama-docs.pdf`.

- **30** in-document (15 exact + 15 paraphrase)
- **25** off-document (15 unrelated + 10 same-topic but not documented)
- **10** ambiguous / borderline

Run from the project root, with Ollama running:

```text
python eval/run_grounding_eval.py
python eval/run_grounding_eval.py --output eval/grounding_after.json
```

Chat path is the same as production: `search_chunks` → `answer_from_documents` → Ollama.

This run used `nomic-embed-text` and the installed `llama3.2` chat model.

## Before vs after

Baseline used the **original** cosine-only chat path and the **old** 96-chunk index (no cleanup, no grounding gate). After used cleaned/re-chunked **99** chunks plus the new retrieval and grounding stack.

| Metric | Baseline | After | Change |
|---|---|---|---|
| In-document recall | 23/30 = **76.7%** | 22/30 = **73.3%** | -3.3 pts |
| Off-document rejection rate | 5/25 = **20.0%** | 23/25 = **92.0%** | +72.0 pts |
| False rejects | 2 | 3 | +1 |
| False accepts | 25 | 3 | -22 |
| Ambiguous pass | 2/10 = 20.0% | 6/10 = 60.0% | +40.0 pts |
| Overall | 30/65 = 46.2% | 51/65 = **78.5%** | +32.3 pts |

By category (after):

| Category | After |
|---|---|
| Exact in-document | 12/15 = 80.0% |
| Paraphrase in-document | 10/15 = 66.7% |
| Unrelated off-document | 14/15 = 93.3% |
| Same-topic not documented | 9/10 = 90.0% |
| Ambiguous | 6/10 = 60.0% |

## Boss questions (after)

| Question | Expected | Result |
|---|---|---|
| What two values can the format parameter take? | Answer from docs | **PASS** |
| What does the raw parameter do? | Answer from docs | **PASS** |
| What does raw=true do? | Answer from docs (paraphrase) | **PASS** |
| What's the pricing for Ollama Cloud? | Reject | **PASS** |
| How do I configure OAuth for the Ollama API? | Reject | **PASS** |

## PDF cleanup (reprocessed index)

| | Before | After |
|---|---|---|
| Chunks | 96 | 99 |
| `&bull;` entities | 349 in raw extract | 0 |
| Min chunk length | 33 (stub headers) | 233 |
| Page metadata | none | all 99 chunks |
| Section labels | none | 63 chunks |
| `format` / `raw` definitions | split / noisy | kept on page 4, section `Advanced parameters (optional):` |

## Northline regression (`eval/run_eval.py --retrieval-only`)

Policy PDFs still retrieve correctly. Keyword-filter mode (eval only, not chat) is unchanged.

| Mode | Retrieval | In-docs | Out-of-docs |
|---|---|---|---|
| Cosine path | 36/42 = 85.7% | 36/36 = **100%** | 0/6 |
| Cosine + keywords | 42/42 = **100%** | 36/36 = **100%** | 6/6 = **100%** |

Out-of-docs on the cosine path is 0% in this harness because it scores “zero chunks retrieved.” Chat now refuses those questions later (score floor, missing-concept, fact-check), not by returning no chunks.

## Remaining limitations

- The local 1B-class chat model still paraphrases (`5 minutes` vs `5m`) and sometimes omits the exact path (`POST` without `/api/generate`).
- Three in-document false rejects remain, mainly when the conventions / version / embeddings-superseded sections lose the prompt-slot race against higher-cosine example blobs.
- Two off-document leaks remain (`sourdough bread`, Slack) where the model answers from general knowledge using words that also appear in API examples.
- `GROUNDED: YES/NO` is unreliable on the small chat model; the system retries with a simpler prompt when question terms are present in context. A larger chat model would reduce that extra call.
