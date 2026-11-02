from talo.evidence import detect, detection_scores
from talo.synth import generate


def test_detects_every_injected_issue_exactly():
    df, manifest = generate()
    findings = detect(df)
    assert detection_scores(manifest, findings) == {"recall": 1.0, "precision": 1.0}


def test_clean_data_has_no_findings():
    df, _ = generate(issues=())
    assert detect(df) == []


def test_alert_spike_is_localised_to_ussd():
    df, _ = generate()
    segs = [f.segment for f in detect(df) if f.kind == "alert_rate_shift"]
    assert segs == ["channel=ussd"]


def test_deterministic_and_stable_ids():
    a, b = detect(generate()[0]), detect(generate()[0])
    assert a == b and a[0].id == "E-001"
