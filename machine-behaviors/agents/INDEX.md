# OC-Agent corpus — index

One input-analyst agent per machine (1547 total), under `agents/<domain>/`.

| domain | agents |
|---|---|
| agriculture | 71 |
| ai-services | 9 |
| built-space | 165 |
| community-services | 113 |
| data-center | 65 |
| digital-logic | 64 |
| energy | 187 |
| health-personal | 43 |
| health-services | 220 |
| ifrs-accounting | 225 |
| legal-services | 110 |
| life-balance | 110 |
| transportation | 165 |

**total: 1547**

axis grounding: inputSemantics=364, openClawProjection=1183

Conforms to: RealityEngine_Machines corpus-exit-v2.0 (docs/CORPUS_EXIT_CRITERIA.md §3.7). Corpus: 1552 machines, `sha256:4e7af19efcf42268c3bca73899014e255e6e1a905625760f5a159f2636ef7c5d`.

Regenerate: `python3 materialize_agents.py --fresh`.
