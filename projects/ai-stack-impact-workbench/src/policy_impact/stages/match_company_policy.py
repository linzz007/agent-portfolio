"""Stage: match company facts to policy clauses."""

from __future__ import annotations

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.rag.lexical import tokenize


def _fact_text(fact: dict) -> str:
    return f"{fact.get('value', '')} {fact.get('text', '')} {' '.join(str(x) for x in fact.get('policy_relevance', []))}"


def _fact_hit(clause_terms: set[str], fact: dict) -> dict | None:
    fact_terms = set(tokenize(_fact_text(fact)))
    if not fact_terms:
        return None
    overlap = clause_terms & fact_terms
    policy_relevance_terms = set(tokenize(" ".join(str(x) for x in fact.get("policy_relevance", []))))
    relevance_overlap = clause_terms & policy_relevance_terms
    dice = (2 * len(overlap)) / max(len(clause_terms) + len(fact_terms), 1)
    relevance_coverage = len(relevance_overlap) / max(len(policy_relevance_terms), 1)
    score = min(1.0, dice * 0.65 + relevance_coverage * 0.35)
    if not overlap or score < 0.02:
        return None
    return {
        "fact_id": fact.get("fact_id"),
        "source_path": fact.get("source_path"),
        "source": fact.get("source"),
        "source_kind": fact.get("source_kind", "other"),
        "value": fact.get("value"),
        "importance": fact.get("importance", 3),
        "confidence": fact.get("confidence", 0.5),
        "overlap_terms": sorted(overlap)[:10],
        "semantic_score": round(score, 4),
    }


def run(state: PolicyImpactState, gateway: ToolGateway | None = None) -> PolicyImpactState:
    gateway = gateway or ToolGateway()
    facts = state.company_context_pack.get("pinned_facts", []) + state.company_context_pack.get("retrieved_company_facts", [])
    matches = []
    seen = set()
    for clause in state.policy_clauses:
        clause_terms = set(tokenize(clause.get("text", "")))
        query = f"{clause.get('policy_title', '')} {clause.get('clause_type', '')} {clause.get('text', '')}"
        tool_facts = gateway.call(
            "match_company_policy",
            "company_wiki_search",
            company_id=state.company_id,
            query=query,
            top_k=8,
        )
        candidate_facts = list(facts)
        existing_fact_ids = {fact.get("fact_id") for fact in candidate_facts}
        for fact in tool_facts:
            if fact.get("fact_id") not in existing_fact_ids:
                candidate_facts.append(fact)
                existing_fact_ids.add(fact.get("fact_id"))

        fact_hits = []
        for fact in candidate_facts:
            key = (clause.get("clause_id"), fact.get("fact_id"))
            if key in seen:
                continue
            hit = _fact_hit(clause_terms, fact)
            if not hit:
                continue
            seen.add(key)
            fact_hits.append(hit)
        if fact_hits:
            fact_hits.sort(
                key=lambda item: (
                    float(item.get("semantic_score", 0)),
                    int(item.get("importance", 3)),
                    float(item.get("confidence", 0.5)),
                ),
                reverse=True,
            )
            matches.append(
                {
                    "match_id": f"match_{len(matches) + 1:03d}",
                    "policy_id": clause.get("policy_id"),
                    "policy_title": clause.get("policy_title"),
                    "clause_id": clause.get("clause_id"),
                    "clause_type": clause.get("clause_type"),
                    "policy_evidence": clause,
                    "company_evidence": fact_hits[:5],
                    "match_quality": {
                        "max_semantic_score": max(float(x.get("semantic_score", 0)) for x in fact_hits),
                        "evidence_count": len(fact_hits),
                    },
                }
            )
    state.policy_matches = matches
    write_json_artifact(
        state,
        "policy_matches",
        state.policy_matches,
        "data/policies/processed",
        "policy_matches.json",
    )
    return state
