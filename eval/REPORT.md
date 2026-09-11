# RAG evaluation report

42 questions in `eval/qa_pairs.json`, written from `return_policy.pdf` and `warranty.pdf`.

- **36** questions whose answers are in those PDFs
- **6** questions that are **not** in the PDFs (the bot should refuse)

## How the script scores

`python eval/run_eval.py`

1. **Retrieval:** did `search_chunks` return text that contains the expected facts?
   - In-doc question: every `must_contain` string is in the retrieved chunks
   - Out-of-doc question: **zero** chunks retrieved
2. **Grounding:** did the model answer stay on that text?
   - In-doc: the expected facts appear in the answer
   - Out-of-doc: the answer contains `I could not find that in the uploaded support documents.`

Same path as chat: `search_chunks` → `answer_from_documents` → Ollama.

This run used `nomic-embed-text` and `llama3.2:1b` (the chat model installed on this machine).

## Baseline (cosine only)

This is the original retriever: keep a chunk if cosine similarity ≥ `0.36`.

| Metric | Score |
|---|---|
| Retrieval (all 42) | **37/42 = 88.1%** |
| Retrieval in-docs | 36/36 = 100% |
| Retrieval out-of-docs | 1/6 = 16.7% |
| Grounded (all 42) | **31/42 = 73.8%** |

Off-topic questions like “Who is the CEO of Northline?” still matched a PDF because the embedding is close to the word *Northline*. The model then answered from that text instead of saying it did not know.

## One improvement

**Keyword filter** in `search_chunks` (`require_keywords=True`, now the default).

A chunk is kept only if:

1. cosine ≥ 0.36, **and**
2. the chunk shares numbers or real words with the question (`_keyword_match`)

That blocks “CEO of Northline” from using the warranty PDF just because both say Northline.

## After the change

| Metric | Baseline | Improved | Change |
|---|---|---|---|
| Retrieval | 88.1% | **100%** | +11.9 pts |
| Retrieval in-docs | 100% | 100% | same |
| Retrieval out-of-docs | 16.7% | **100%** | +83.3 pts |
| Grounded | 73.8% | **83.3%** | +9.5 pts |

Grounding is not 100% because scoring is **exact phrases** (for example `24 month` vs `24 months`). The 1B chat model also rephrases. The big win is off-topic questions: they now return no chunks, so the bot says it could not find that in the uploaded documents.

## How to re-run

```text
python eval/run_eval.py --retrieval-only
python eval/run_eval.py
```
