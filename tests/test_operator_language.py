from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_normalize_operator_language_prefers_primary_tag():
    import operator_language

    assert operator_language.normalize_operator_language("es-ES") == "es"
    assert operator_language.normalize_operator_language("zh_CN") == "zh"
    assert operator_language.normalize_operator_language("") == ""


def test_append_operator_language_contract_is_idempotent():
    import operator_language

    prompt = "Run nexo_stop now."
    once = operator_language.append_operator_language_contract(prompt, "es")
    twice = operator_language.append_operator_language_contract(once, "es")

    assert "Spanish (es)" in once
    assert twice == once
