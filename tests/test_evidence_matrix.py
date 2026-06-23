from evidence_matrix import validate_claim


def test_sensitive_root_cause_without_evidence_needs_evidence():
    result = validate_claim("La causa es el firewall de produccion.")
    assert result.verdict == "needs_evidence"
    assert "firewall" in result.domains


def test_explicit_evidence_passes():
    result = validate_claim("La causa es el token caducado de produccion.", evidence_refs=["log:abc"])
    assert result.verdict == "pass"
    assert result.evidence_count == 1


def test_ledger_hit_passes():
    result = validate_claim("La causa es la cuota de OpenAI.", search=lambda _q: ["query:1"])
    assert result.verdict == "pass"


def test_hedged_claim_not_applicable():
    result = validate_claim("Podria ser el proveedor de pagos.")
    assert result.verdict == "not_applicable"


def test_non_sensitive_claim_not_applicable():
    result = validate_claim("La causa es un typo local.")
    assert result.verdict == "not_applicable"


def test_question_not_applicable():
    result = validate_claim("La causa es Stripe?")
    assert result.verdict == "not_applicable"

