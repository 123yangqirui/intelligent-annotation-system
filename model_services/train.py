"""只被受控训练任务调用，需在 training Worker 安装 ML 依赖。"""

import json
from pathlib import Path
import sys

kind, path = sys.argv[1:]
request = json.loads(Path(path).read_text())
config = request["config"]
output = Path(request["output"])
output.mkdir(parents=True, exist_ok=True)
if kind == "text_classification":
    import numpy as np
    from datasets import Dataset
    from transformers import (
        AutoTokenizer,
        AutoModelForSequenceClassification,
        Trainer,
        TrainingArguments,
        set_seed,
    )
    from sklearn.metrics import accuracy_score, f1_score

    set_seed(int(config.get("seed", 42)))
    checkpoint = config["checkpoint"]
    labels = request["labels"]
    mapping = {x["id"]: i for i, x in enumerate(labels)}
    tokenizer = AutoTokenizer.from_pretrained(checkpoint)
    model = AutoModelForSequenceClassification.from_pretrained(
        checkpoint,
        num_labels=len(labels),
        id2label={i: x["name"] for i, x in enumerate(labels)},
        label2id={x["name"]: i for i, x in enumerate(labels)},
        ignore_mismatched_sizes=True,
    )

    def dataset(split):
        rows = [
            {"text": x["text"], "labels": mapping[x["label_id"]]}
            for x in request["rows"]
            if x["split"] == split
        ]
        return Dataset.from_list(rows).map(
            lambda x: tokenizer(
                x["text"],
                truncation=True,
                padding="max_length",
                max_length=int(config.get("max_length", 256)),
            ),
            batched=True,
        )

    def metrics(result):
        guess = np.argmax(result.predictions, axis=-1)
        return {
            "accuracy": accuracy_score(result.label_ids, guess),
            "macro_f1": f1_score(result.label_ids, guess, average="macro"),
        }

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(output),
            num_train_epochs=int(config.get("epochs", 3)),
            per_device_train_batch_size=int(config.get("batch_size", 8)),
            per_device_eval_batch_size=int(config.get("batch_size", 8)),
            learning_rate=float(config.get("learning_rate", 2e-5)),
            eval_strategy="epoch",
            report_to=[],
        ),
        train_dataset=dataset("train"),
        eval_dataset=dataset("val"),
        compute_metrics=metrics,
    )
    trainer.train()
    result = trainer.evaluate()
    trainer.save_model(str(output))
    tokenizer.save_pretrained(str(output))
else:
    from ultralytics import YOLO
    import yaml

    data_path = Path(request["data_yaml"])
    data = yaml.safe_load(data_path.read_text())
    data["path"] = str(data_path.parent)
    data_path.write_text(yaml.safe_dump(data))
    model = YOLO(config["checkpoint"])
    metrics = model.train(
        data=str(data_path),
        epochs=int(config.get("epochs", 3)),
        batch=int(config.get("batch_size", 8)),
        imgsz=int(config.get("image_size", 640)),
        project=str(output),
        name="run",
        seed=int(config.get("seed", 42)),
    )
    result = {k: float(v) for k, v in metrics.results_dict.items()}
Path(path).parent.joinpath("metrics.json").write_text(json.dumps(result, default=float))
