from closure_promise_audit import audit_closure


def test_promise_without_followup_creates_debt():
    result = audit_closure("Lo dejo registrado y lo retomo mañana.")
    assert not result.ok
    assert result.signals[0].recommended_action == "create_followup"


def test_persisted_promise_passes():
    result = audit_closure("Te aviso luego.", has_followup_for=lambda _: True)
    assert result.ok
    assert result.debt_count == 0


def test_user_correction_without_learning_creates_debt():
    result = audit_closure("", "No, eso no es asi.", has_learning_for=lambda _: False)
    assert result.signals[0].kind == "correction"
    assert result.signals[0].recommended_action == "create_learning"


def test_brain_down_marks_all_signals_as_debt():
    result = audit_closure("Lo dejo registrado.", "Te equivocas.", brain_down=True)
    assert result.debt_count == 2
    assert {signal.reason for signal in result.signals} == {"brain_down"}


def test_capture_checker_failure_is_fail_closed():
    def broken(_text):
        raise RuntimeError("db down")

    result = audit_closure("Te aviso luego.", has_followup_for=broken)
    assert not result.ok
    assert result.signals[0].reason.startswith("capture_check_failed")


def test_no_signal_is_ok():
    result = audit_closure("Cierre verificado.", "Gracias.")
    assert result.ok
    assert result.signals == []

