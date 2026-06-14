"""SENSITIVE_NAME_MARKERS must token-match, not substring-match (Release A / A6).

Substring matching flagged legitimate files: 'secret' inside 'secretaria.pdf' or
'informe_secreto.docx' wrongly marked them sensitive and excluded real documents
from the index. Match whole tokens instead.
"""

from local_context import privacy


def test_sensitive_path_does_not_substring_flag_legit_names():
    assert privacy.is_sensitive_path("/Users/x/Documents/secretaria.pdf") is False
    assert privacy.is_sensitive_path("/Users/x/Documents/informe_secreto.docx") is False


def test_sensitive_path_still_flags_real_secret_tokens():
    assert privacy.is_sensitive_path("/Users/x/api_secret_key.txt") is True
    assert privacy.is_sensitive_path("/Users/x/bearer_token.json") is True
    assert privacy.is_sensitive_path("/Users/x/password.txt") is True
    assert privacy.is_sensitive_path("/Users/x/my_api_key.json") is True
