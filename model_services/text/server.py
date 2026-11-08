"""BERT 等序列分类检查点服务；必须已有训练好的分类头。"""

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

checkpoint = os.environ["CHECKPOINT"]
tokenizer = AutoTokenizer.from_pretrained(checkpoint)
model = AutoModelForSequenceClassification.from_pretrained(checkpoint)
model.eval()
device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device)
app = FastAPI(title="Text classification model")


class Predict(BaseModel):
    text: str
    labels: list[dict]
    rules: dict = {}


@app.get("/health")
def health():
    return {"status": "ok", "checkpoint": checkpoint, "device": device}


@app.post("/predict")
def predict(body: Predict):
    encoded = tokenizer(body.text, return_tensors="pt", truncation=True, max_length=512)
    with torch.inference_mode():
        scores = torch.softmax(
            model(**{k: v.to(device) for k, v in encoded.items()}).logits, dim=-1
        )[0]
    index = int(scores.argmax())
    name = model.config.id2label[index]
    label = next((x for x in body.labels if x["name"] == name), None)
    if not label:
        raise HTTPException(
            422, "检查点 id2label 与项目标签名称不匹配；请先配置或训练分类头。"
        )
    return {
        "content": {"task_type": "text_classification", "label_id": label["id"]},
        "signals": {
            "score": float(scores[index]),
            "score_kind": "uncalibrated",
            "checkpoint": checkpoint,
        },
    }
