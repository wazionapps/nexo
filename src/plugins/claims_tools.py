"""Claims/wiki plugin — public surface over NEXO claim graph."""

import claim_graph


def handle_claim_add(
    text: str,
    domain: str = "",
    evidence: str = "",
    confidence: float = 1.0,
    source_type: str = "",
    source_id: str = "",
    freshness_days: int = 30,
) -> str:
    result = claim_graph.add_claim(
        text=text,
        domain=domain,
        evidence=evidence,
        confidence=confidence,
        source_type=source_type,
        source_id=source_id,
        freshness_days=freshness_days,
    )
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return f"Claim #{result['id']} {result['action']} (confidence={result['confidence']})"


def handle_claim_search(query: str = "", domain: str = "", status: str = "", limit: int = 20) -> str:
    items = claim_graph.search_claims(query=query, domain=domain, status=status, limit=limit)
    if not items:
        return "No claims found."
    lines = [f"CLAIMS — {len(items)} result(s):", ""]
    for item in items:
        lines.append(
            f"  #{item['id']} [{item.get('verification_status','unverified')}] "
            f"freshness={item.get('freshness_state','?')}({item.get('freshness_score',0)})"
        )
        lines.append(f"    {item['text'][:220]}")
        if item.get("evidence"):
            lines.append(f"    evidence: {str(item['evidence'])[:180]}")
        if item.get("domain"):
            lines.append(f"    domain: {item['domain']}")
    return "\n".join(lines)


def handle_claim_get(claim_id: int) -> str:
    item = claim_graph.get_claim(claim_id)
    if not item:
        return f"Claim #{claim_id} not found."
    lines = [
        f"CLAIM #{item['id']}",
        f"  status: {item.get('verification_status', 'unverified')}",
        f"  confidence: {item.get('confidence', 0)}",
        f"  freshness: {item.get('freshness_state', '?')} ({item.get('freshness_score', 0)})",
        f"  age_days: {item.get('age_days', 0)}",
        f"  domain: {item.get('domain', '') or 'n/a'}",
        f"  source: {item.get('source_type', '')}:{item.get('source_id', '')}",
        f"  text: {item.get('text', '')}",
    ]
    if item.get("evidence"):
        lines.append(f"  evidence: {item['evidence']}")
    if item.get("links_out"):
        lines.append(f"  links_out: {len(item['links_out'])}")
    if item.get("links_in"):
        lines.append(f"  links_in: {len(item['links_in'])}")
    return "\n".join(lines)


def handle_claim_link(source_claim_id: int, target_claim_id: int, relation: str, confidence: float = 1.0) -> str:
    result = claim_graph.link_claims(source_claim_id, target_claim_id, relation, confidence=confidence)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return f"Linked claim #{source_claim_id} -> #{target_claim_id} [{relation}]"


def handle_claim_verify(claim_id: int, status: str = "confirmed") -> str:
    result = claim_graph.verify_claim(claim_id, status=status)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return (
        f"Claim #{result['id']} now {result['verification_status']} "
        f"(freshness={result.get('freshness_state', '?')} {result.get('freshness_score', 0)})"
    )


def handle_claim_lint(max_age_days: int = 30, limit: int = 20) -> str:
    items = claim_graph.lint_claims(max_age_days=max_age_days, limit=limit)
    if not items:
        return "Claim lint: no attention items."
    lines = [f"CLAIM LINT — {len(items)} attention item(s):", ""]
    for item in items:
        lines.append(f"  #{item['id']} [{', '.join(item.get('lint_reasons', []))}]")
        lines.append(f"    {item['text'][:220]}")
    return "\n".join(lines)


def handle_claim_stats() -> str:
    stats = claim_graph.stats()
    return (
        "CLAIM GRAPH STATS\n"
        f"  total: {stats['total_claims']}\n"
        f"  links: {stats['total_links']}\n"
        f"  contradictions: {stats['contradictions']}\n"
        f"  lint_attention: {stats['lint_attention']}\n"
        f"  by_status: {stats['by_status']}\n"
        f"  by_domain: {stats['by_domain']}"
    )


TOOLS = [
    (handle_claim_add, "nexo_claim_add", "Add a structured claim with provenance, evidence, and freshness."),
    (handle_claim_search, "nexo_claim_search", "Search claims by meaning or filters."),
    (handle_claim_get, "nexo_claim_get", "Get a single claim with its links and freshness metadata."),
    (handle_claim_link, "nexo_claim_link", "Link two claims with supports/contradicts/refines/supersedes."),
    (handle_claim_verify, "nexo_claim_verify", "Verify or reclassify a claim state."),
    (handle_claim_lint, "nexo_claim_lint", "Audit stale, weak, contradictory, or evidence-poor claims."),
    (handle_claim_stats, "nexo_claim_stats", "Claim graph statistics and attention counts."),
]
