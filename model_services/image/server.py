import base64
import io
import os
from PIL import Image
from ultralytics import YOLO
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from uuid import uuid4

checkpoint = os.environ["CHECKPOINT"]
model = YOLO(checkpoint)
app = FastAPI(title="YOLO detection model")


class Predict(BaseModel):
    image_base64: str
    labels: list[dict]


@app.get("/health")
def health():
    return {"status": "ok", "checkpoint": checkpoint}


@app.post("/predict")
def predict(body: Predict):
    try:
        data = base64.b64decode(body.image_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "图片编码无效")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "图片过大")
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 40000000:
            raise HTTPException(413, "像素过大")
        image = image.convert("RGB")
    except Image.DecompressionBombError:
        raise HTTPException(413, "像素过大")
    except (OSError, ValueError):
        raise HTTPException(422, "无法解码图片")
    result = model.predict(image, verbose=False)[0]
    labels = {x["name"]: x["id"] for x in body.labels}
    boxes = []
    for box in result.boxes:
        name = result.names[int(box.cls[0])]
        if name not in labels:
            continue
        boxes.append(
            {
                "object_id": str(uuid4()),
                "label_id": labels[name],
                "xyxy": box.xyxy[0].tolist(),
                "score": float(box.conf[0]),
            }
        )
    return {
        "content": {
            "task_type": "image_detection",
            "coordinate_space": "original_image_pixels",
            "explicit_empty": not boxes,
            "boxes": boxes,
        },
        "signals": {
            "score_kind": "uncalibrated",
            "checkpoint": checkpoint,
            "scope": "only classes mapped by name",
        },
    }
