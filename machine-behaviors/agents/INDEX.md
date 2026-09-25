# OC-Agent corpus — index

One input-analyst agent per machine (1323 total), under `agents/<domain>/`.

| domain | agents |
|---|---|
| agriculture | 71 |
| ai-services | 9 |
| built-space | 165 |
| community-services | 113 |
| data-center | 65 |
| digital-logic | 65 |
| energy | 187 |
| health-personal | 43 |
| health-services | 220 |
| legal-services | 110 |
| life-balance | 110 |
| transportation | 165 |

**total: 1323**

axis grounding: inputSemantics=139, openClawProjection=1184

Conforms to: RealityEngine_Machines corpus-exit-v1.0 (docs/CORPUS_EXIT_CRITERIA.md §3.7). Corpus: 1328 machines, `sha256:cba104f1972db03bb925e51d478541ceb47711854b8a254c04a8d0c6d7ab2e0c`.

Regenerate: `python3 materialize_agents.py --fresh`.
