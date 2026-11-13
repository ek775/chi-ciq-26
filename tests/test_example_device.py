import sys
from types import SimpleNamespace

import pytest

from examples import transcribe


@pytest.mark.parametrize("available, expected", [(True, "cuda"), (False, "cpu")])
def test_workflow_selects_device_for_all_models(
    monkeypatch, capsys, available, expected
):
    checks = []

    def is_available():
        checks.append(True)
        return available

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=is_available)),
    )
    calls = {}

    def adapter(name):
        def create(*args, **kwargs):
            calls[name] = (args, kwargs)
            if name == "StreamingPyannoteDiarizer":
                return SimpleNamespace(diarize_chunks=lambda *a: iter(()))
            return name

        return create

    for name in (
        "StreamingPyannoteDiarizer",
        "WhisperTranscriber",
        "SentenceTextEmbedder",
        "PyannoteVoiceEmbedder",
    ):
        monkeypatch.setattr(transcribe, name, adapter(name))
    store = object()
    workflow = transcribe.build_workflow(store, "test-token")
    assert workflow.store is store
    assert checks == [True]
    assert len(calls) == 4
    assert all(kwargs["device"] == expected for _, kwargs in calls.values())
    assert calls["WhisperTranscriber"][1]["language"] == "en"
    assert calls["StreamingPyannoteDiarizer"][0] == ("test-token",)
    assert calls["PyannoteVoiceEmbedder"][0] == ("test-token",)
    assert f"Inference device: {expected}" in capsys.readouterr().out


def test_gpu_model_errors_propagate(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
    )

    def fail(*args, **kwargs):
        assert kwargs["device"] == "cuda"
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(transcribe, "StreamingPyannoteDiarizer", fail)
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        transcribe.build_workflow(object(), "test-token")
