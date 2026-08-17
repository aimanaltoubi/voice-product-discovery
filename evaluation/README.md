# Evaluation - measuring the system's accuracy and readiness

This folder holds the standalone evaluation for the project:

- evaluation.ipynb - runs a fixed set of graded cases through the real pipeline and scores every model
- evaluation_report.json and evaluation_cases.csv are written by the notebook after a run

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/aimanaltoubi/voice-product-discovery/blob/main/evaluation/evaluation.ipynb)

## What is measured

- speech to text: WER and CER against known reference sentences plus an accent version (the Whisper paper measures)
- router: accuracy + precision and recall and F1 per class + a confusion matrix + constraint extraction accuracy
- retrieval: Precision at 3 + MRR + NDCG at 3 on labeled probe queries (classic ranking measures per Manning et al.)
- answers: faithfulness and relevance scored by a judge model in the RAGAS style (Es et al. 2023)
- cross-cutting: latency budgets per stage (router and safety and retrieval 8 s - answer 12 s) and a pass or fail verdict per case rolled up by category
- ProofAgent Harness to evaluate the agents across these six metrics:

   Task Success
    
    Hallucination Resistance
    
    Safety
    
    Instruction Following
    
    Manipulation Resistance
    
    Tool Use

The grading catalog is the bundled 24-product set because exact grading needs an exact answer key. Part 2 of the notebook switches it to the Kaggle dataset.

## Latest measured results

| measure | result | target |
|---|---|---|
| ASR WER (average of 4 sentences) | - | 10% or less |
| ASR CER (average of 4 sentences) | - | 5% or less |
| ASR WER accent | - | 20% or less |
| Router accuracy | - | 90% or more |
| Router macro F1 | - | 0.85 or more |
| Constraint extraction accuracy | - | 85% or more |
| Retrieval Precision@3 | - | 0.8 or more |
| Retrieval MRR | - | 0.8 or more |
| Retrieval NDCG@3 | - | 0.8 or more |
| Answer faithfulness (judge) | - | 90% or more |
| Answer relevance (judge) | - | 0.8 or more |
| Latency budget compliance | - | 90% or more |
| Case accuracy - overall | - | 90% or more |
