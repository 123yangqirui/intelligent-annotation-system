"""固定训练快照与独立评估，不把评估真值交给预测模型。"""

import io
import json
from pathlib import Path
import subprocess
import sys
import math
from sqlalchemy import select
from .models import (
    Item,
    Revision,
    Reference,
    LabelSchema,
    ModelSpec,
    Export,
    DatasetItem,
)
from .config import settings
from . import storage


def classification_metrics(expected, predicted, labels):
    if not expected or len(expected) != len(predicted) or not labels:
        raise ValueError("没有可评估标签")
    matrix = {x: {y: 0 for y in labels} for x in labels}
    for truth, guess in zip(expected, predicted):
        if truth in matrix and guess in matrix[truth]:
            matrix[truth][guess] += 1
    f1 = []
    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[x][label] for x in labels if x != label)
        fn = sum(matrix[label][x] for x in labels if x != label)
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    return {
        "accuracy": sum(x == y for x, y in zip(expected, predicted)) / len(expected),
        "macro_f1": sum(f1) / len(f1),
        "count": len(expected),
        "confusion_matrix": matrix,
    }


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0


def detection_counts(truth, prediction):
    used = set()
    tp = 0
    for predicted in prediction:
        matches = [
            (iou(predicted["xyxy"], actual["xyxy"]), i)
            for i, actual in enumerate(truth)
            if i not in used and actual["label_id"] == predicted["label_id"]
        ]
        if matches:
            value, index = max(matches)
            if value >= 0.5:
                tp += 1
                used.add(index)
    return tp, len(prediction) - tp, len(truth) - tp


def detection_map(records, labels):
    """按 IoU 0.50:0.05:0.95 和 101 点插值计算 AP；需要模型分数。"""
    if any(
        len(scores) != len(prediction)
        or any(
            not isinstance(x, (int, float))
            or isinstance(x, bool)
            or not math.isfinite(x)
            or not 0 <= x <= 1
            for x in scores
        )
        for _, prediction, scores in records
    ):
        return {
            "map_50": None,
            "map_50_95": None,
            "map_note": "模型没有提供逐框分数，无法进行 AP 排序。",
        }
    values = []
    for threshold in [0.5 + 0.05 * i for i in range(10)]:
        per_class = []
        for label in labels:
            total = sum(
                sum(x["label_id"] == label for x in truth) for truth, _, _ in records
            )
            if not total:
                continue
            predictions = []
            for image_id, (_, prediction, scores) in enumerate(records):
                predictions.extend(
                    (score, image_id, box)
                    for box, score in zip(prediction, scores)
                    if box["label_id"] == label
                )
            predictions.sort(key=lambda x: x[0], reverse=True)
            used = set()
            tp = 0
            fp = 0
            curve = []
            for score, image_id, box in predictions:
                matches = [
                    (iou(box["xyxy"], actual["xyxy"]), index)
                    for index, actual in enumerate(records[image_id][0])
                    if actual["label_id"] == label and (image_id, index) not in used
                ]
                if matches and max(matches)[0] >= threshold:
                    tp += 1
                    used.add((image_id, max(matches)[1]))
                else:
                    fp += 1
                curve.append((tp / total, tp / (tp + fp)))
            per_class.append(
                sum(
                    max(
                        (precision for recall, precision in curve if recall >= r / 100),
                        default=0,
                    )
                    for r in range(101)
                )
                / 101
            )
        values.append(sum(per_class) / len(per_class) if per_class else 0)
    return {
        "map_50": values[0],
        "map_50_95": sum(values) / len(values),
        "map_note": "101-point AP; IoU 0.50:0.05:0.95; no COCO area/maxDets stratification",
    }


def validate_training_config(config):
    import re

    ranges = {
        "epochs": (1, 100),
        "batch_size": (1, 128),
        "seed": (0, 2**32 - 1),
        "max_length": (8, 4096),
        "image_size": (32, 4096),
    }
    if set(config) - set(ranges) - {"checkpoint", "learning_rate"}:
        raise ValueError("未知训练参数")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?",
        str(config.get("checkpoint", "")),
    ):
        raise ValueError("需要合法检查点名称")
    for key, (low, high) in ranges.items():
        if key in config and (
            type(config[key]) is not int or not low <= config[key] <= high
        ):
            raise ValueError(f"{key} 须为 {low}–{high} 之间的整数")
    if "learning_rate" in config and (
        type(config["learning_rate"]) not in (int, float)
        or not math.isfinite(config["learning_rate"])
        or not 0 < config["learning_rate"] <= 1
    ):
        raise ValueError("learning_rate 须为 0–1 之间的有限正数")


def run_experiment(db, job, project, check=lambda: None):
    schema = db.get(LabelSchema, job.config["schema_id"])
    if job.type == "evaluation":
        from .inference import predict

        model = db.get(ModelSpec, job.config["model_id"])
        expected = []
        predicted = []
        counts = [0, 0, 0]
        records = []
        ids = job.config.get("item_ids")
        if ids is None:
            ids = db.scalars(
                select(DatasetItem.item_id).where(
                    DatasetItem.dataset_id == job.config["dataset_id"]
                )
            ).all()
        for item_id in ids:
            check()
            reference = db.scalar(
                select(Reference).where(
                    Reference.item_id == item_id, Reference.schema_id == schema.id
                )
            )
            if not reference:
                continue
            item = db.get(Item, item_id)
            content, signals = predict(db, model, job, project, schema, item)
            if project.task_type == "text_classification":
                expected.append(reference.content["label_id"])
                predicted.append(content["label_id"])
            else:
                counts = [
                    a + b
                    for a, b in zip(
                        counts,
                        detection_counts(reference.content["boxes"], content["boxes"]),
                    )
                ]
                records.append(
                    (
                        reference.content["boxes"],
                        content["boxes"],
                        signals.get("box_scores", []),
                    )
                )
        if project.task_type == "text_classification":
            return classification_metrics(
                expected, predicted, [x["id"] for x in schema.labels]
            )
        tp, fp, fn = counts
        if not records:
            raise ValueError("没有可评估参考标注")
        return {
            "metric": "IoU 0.5 matching and AP",
            "images": len(records),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": tp / (tp + fp) if tp + fp else 0,
            "recall": tp / (tp + fn) if tp + fn else 0,
            **detection_map(records, [x["id"] for x in schema.labels]),
        }
    config = job.config.get("config", {})
    validate_training_config(config)
    allowed = {
        "checkpoint",
        "epochs",
        "batch_size",
        "learning_rate",
        "seed",
        "max_length",
        "image_size",
    }
    if set(config) - allowed:
        raise ValueError("未知训练参数")
    checkpoint = config.get("checkpoint", "")
    import re

    if not re.fullmatch(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?", checkpoint):
        raise ValueError("需要合法检查点名称")
    epochs = int(config.get("epochs", 3))
    batch = int(config.get("batch_size", 8))
    if not 1 <= epochs <= 100 or not 1 <= batch <= 128:
        raise ValueError("训练参数超出限制")
    directory = settings().storage_root / "training" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = job.config["snapshot"]
    if not any(x["split"] == "train" for x in snapshot) or not any(
        x["split"] == "val" for x in snapshot
    ):
        raise ValueError("需要分别发布 train 和 val 划分的已审核样本")
    request = {
        "labels": schema.labels,
        "config": config,
        "rows": [],
        "output": str(directory / "model"),
    }
    if project.task_type == "text_classification":
        for entry in snapshot:
            item = db.get(Item, entry["item_id"])
            revision = db.get(Revision, entry["revision_id"])
            if entry["split"] == "test":
                continue
            request["rows"].append(
                {
                    "text": item.text,
                    "label_id": revision.content["label_id"],
                    "split": entry["split"],
                }
            )
    else:
        from .imports import export_data

        export = Export(
            project_id=project.id,
            schema_id=schema.id,
            snapshot=[x for x in snapshot if x["split"] != "test"],
            format="yolo",
        )
        db.add(export)
        db.flush()
        export_data(db, export)
        import zipfile

        with zipfile.ZipFile(io.BytesIO(storage.read(export.object_key))) as archive:
            archive.extractall(directory / "dataset")
        request["data_yaml"] = str(directory / "dataset" / "data.yaml")
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False))
    script = Path(__file__).resolve().parents[2] / "model_services" / "train.py"
    python = settings().training_python or sys.executable
    modules = (
        "import torch, transformers, datasets, accelerate, sklearn"
        if project.task_type == "text_classification"
        else "import ultralytics"
    )
    dependency_check = subprocess.run(
        [python, "-c", modules], capture_output=True, timeout=30
    )
    if dependency_check.returncode:
        (directory / "training.log").write_text(
            dependency_check.stderr.decode(errors="replace")
        )
        raise ValueError(
            "训练解释器缺少 ML 依赖，请按运行说明安装并配置 TRAINING_PYTHON。"
        )
    with (directory / "training.log").open("w") as log:
        run_training(
            [python, str(script), project.task_type, str(request_path)], log, check
        )
    metrics_path = directory / "metrics.json"
    artifact = (
        directory / "model"
        if project.task_type == "text_classification"
        else directory / "model" / "run" / "weights" / "best.pt"
    )
    if not artifact.exists():
        raise ValueError("训练没有生成预期模型文件，请检查训练日志")
    candidate = ModelSpec(
        name=project.name + " · 训练候选",
        adapter="bert" if project.task_type == "text_classification" else "yolo",
        task_type=project.task_type,
        config={
            "checkpoint": "trained:" + job.id,
            "artifact_path": str(artifact),
            "candidate": True,
            "endpoint": "http://127.0.0.1:8101",
        },
    )
    db.add(candidate)
    db.flush()
    return {
        "model_id": candidate.id,
        "checkpoint": "trained:" + job.id,
        "model_directory": str(artifact),
        "metrics": json.loads(metrics_path.read_text())
        if metrics_path.exists()
        else {},
        "log_file": str(directory / "training.log"),
        "status": "candidate",
    }


def run_training(command, log, check):
    import time, os, signal

    process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 86400
    try:
        while process.poll() is None:
            check()
            if time.monotonic() > deadline:
                raise TimeoutError("训练超过 24 小时限制")
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        check()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    except BaseException:
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        raise
