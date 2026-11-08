"""HTTP contracts tested with substituted ML libraries, without downloading weights."""

import base64, contextlib, importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from test_workflow import image_bytes


def load_service(name):
    path = Path(__file__).resolve().parents[2] / "model_services" / name / "server.py"
    spec = importlib.util.spec_from_file_location("fixture_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_yolo_service_validates_input_and_label_mapping(monkeypatch):
    class Tensor:
        def __init__(self, value):
            self.value = value

        def tolist(self):
            return self.value

    box = SimpleNamespace(cls=[0], conf=[0.9], xyxy=[Tensor([1, 2, 30, 40])])
    model = SimpleNamespace(
        predict=lambda *a, **kw: [SimpleNamespace(names={0: "目标"}, boxes=[box])]
    )
    monkeypatch.setitem(
        __import__("sys").modules, "ultralytics", SimpleNamespace(YOLO=lambda _: model)
    )
    monkeypatch.setenv("CHECKPOINT", "test-only")
    service = load_service("image")
    with TestClient(service.app) as client:
        assert client.get("/health").json()["status"] == "ok"
        for value in ("invalid-base64", base64.b64encode(b"not an image").decode()):
            assert (
                client.post(
                    "/predict", json={"image_base64": value, "labels": []}
                ).status_code
                == 422
            )
        image = base64.b64encode(image_bytes()).decode()
        data = client.post(
            "/predict",
            json={"image_base64": image, "labels": [{"name": "目标", "id": "A"}]},
        ).json()
        assert (
            data["content"]["boxes"][0]["label_id"] == "A"
            and data["content"]["boxes"][0]["score"] == 0.9
        )
        data = client.post(
            "/predict",
            json={"image_base64": image, "labels": [{"name": "其他", "id": "B"}]},
        ).json()
        assert data["content"]["explicit_empty"] is True


def test_bert_service_checks_label_names(monkeypatch):
    import sys

    class Scores:
        def argmax(self):
            return 0

        def __getitem__(self, key):
            return 0.9

    class Model:
        config = SimpleNamespace(id2label={0: "类别"})

        def eval(self):
            pass

        def to(self, device):
            pass

        def __call__(self, **kw):
            return SimpleNamespace(logits="fixture")

    tokenizer = lambda *a, **kw: {"input_ids": SimpleNamespace(to=lambda _: None)}
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            inference_mode=contextlib.nullcontext,
            softmax=lambda *a, **kw: [Scores()],
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=lambda _: tokenizer),
            AutoModelForSequenceClassification=SimpleNamespace(
                from_pretrained=lambda _: Model()
            ),
        ),
    )
    monkeypatch.setenv("CHECKPOINT", "test-only")
    service = load_service("text")
    with TestClient(service.app) as client:
        assert client.get("/health").json()["device"] == "cpu"
        assert (
            client.post(
                "/predict",
                json={"text": "样本", "labels": [{"name": "其他", "id": "B"}]},
            ).status_code
            == 422
        )
        data = client.post(
            "/predict", json={"text": "样本", "labels": [{"name": "类别", "id": "A"}]}
        ).json()
        assert (
            data["content"]["label_id"] == "A"
            and data["signals"]["score_kind"] == "uncalibrated"
        )
