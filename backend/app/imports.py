import csv
import hashlib
import io
import json
from pathlib import PurePosixPath
import stat
import zipfile
from PIL import Image, ImageOps
from sqlalchemy import select
from .config import settings
from . import storage
from .models import (
    Dataset,
    DatasetItem,
    Item,
    Reference,
    Revision,
    LabelSchema,
)
from .annotations import validate_content, digest
from .job_control import JobCancelled, LeaseLost


def image_data(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > settings().image_max_pixels:
            raise ValueError("图片像素数超过限制")
        orientation = image.getexif().get(274, 1)
        original_size = image.size
        image = ImageOps.exif_transpose(image).convert("RGB")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue(), image.width, image.height, orientation, original_size


def add_item(db, project, dataset, content, name="", metadata=None):
    if project.task_type == "text_classification":
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > 1_000_000
        ):
            raise ValueError("文本为空或过长")
        hash_value = hashlib.sha256(content.encode()).hexdigest()
        fields = {"text": content}
    else:
        data, w, h, orientation, original_size = image_data(content)
        hash_value = hashlib.sha256(data).hexdigest()
        fields = {"object_key": storage.put(data, "png"), "width": w, "height": h}
        metadata = {
            **(metadata or {}),
            "orientation": orientation,
            "original_size": list(original_size),
        }
    item = db.scalar(
        select(Item).where(
            Item.project_id == project.id, Item.content_hash == hash_value
        )
    )
    if not item:
        item = Item(
            project_id=project.id,
            external_id=name[:256],
            content_hash=hash_value,
            metadata_json=metadata or {},
            **fields,
        )
        db.add(item)
        db.flush()
    if not db.get(DatasetItem, (dataset.id, item.id)):
        db.add(
            DatasetItem(
                dataset_id=dataset.id,
                item_id=item.id,
                split=dataset.config.get("split", "train"),
            )
        )
    return item


def transform_box(coords, item):
    orientation = item.metadata_json.get("orientation", 1)
    w, h = item.metadata_json.get("original_size", [item.width, item.height])

    def point(x, y):
        return {
            1: (x, y),
            2: (w - x, y),
            3: (w - x, h - y),
            4: (x, h - y),
            5: (y, x),
            6: (h - y, x),
            7: (h - y, w - x),
            8: (y, w - x),
        }[orientation]

    x1, y1, x2, y2 = coords
    points = [point(x, y) for x, y in ((x1, y1), (x1, y2), (x2, y1), (x2, y2))]
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def attach_label(db, project, schema, item, content, purpose, job_id):
    normalized = validate_content(project, schema, item, content)
    if purpose == "evaluation":
        old = db.scalar(
            select(Reference).where(
                Reference.item_id == item.id, Reference.schema_id == schema.id
            )
        )
        if not old:
            db.add(Reference(item_id=item.id, schema_id=schema.id, content=normalized))
    elif purpose == "seed":
        op = f"import:{job_id}:{item.id}"
        if not db.scalar(select(Revision).where(Revision.operation_id == op)):
            db.add(
                Revision(
                    project_id=project.id,
                    item_id=item.id,
                    schema_id=schema.id,
                    source="external",
                    operation_id=op,
                    request_hash=digest(normalized),
                    content=normalized,
                )
            )


def run_import(db, project, job, check=lambda: None):
    config = job.config
    dataset = db.get(Dataset, config["dataset_id"])
    schema = (
        db.get(LabelSchema, config.get("schema_id"))
        if config.get("schema_id")
        else None
    )
    purpose = config.get("labels_purpose", "none")
    errors = []
    count = 0
    data = storage.read(config["object_key"])
    filename = config["filename"].lower()

    def text_label(row):
        content = row.get("annotation")
        label_id = (content or {}).get("label_id") or row.get("label_id")
        name = row.get("label_name")
        if schema:
            valid = {x["id"] for x in schema.labels}
            if label_id not in valid:
                label_id = config.get("label_mapping", {}).get(str(label_id)) or next(
                    (x["id"] for x in schema.labels if x["name"] == name), label_id
                )
        return (
            {"task_type": project.task_type, "label_id": label_id} if label_id else None
        )

    def process(name, value, label=None):
        nonlocal count
        check()
        try:
            with db.begin_nested():
                item = add_item(
                    db,
                    project,
                    dataset,
                    value,
                    name,
                    {"source_url": config.get("source_url", "")},
                )
                if project.active_schema_id:
                    from .annotations import get_work

                    get_work(db, item.id, project.active_schema_id)
                if label is not None and purpose != "none":
                    if not schema:
                        raise ValueError("带标签导入需要已发布规范")
                    attach_label(db, project, schema, item, label, purpose, job.id)
                count += 1
        except Exception as error:
            detail = getattr(error, "detail", None)
            errors.append(
                {
                    "source": name,
                    "message": detail.get("message")
                    if isinstance(detail, dict)
                    else str(error)[:300],
                }
            )

    if project.task_type == "text_classification":
        text = data.decode("utf-8-sig")
        text_field = config.get("text_field", "text")
        if filename.endswith(".csv"):
            rows = csv.DictReader(io.StringIO(text))
            for index, row in enumerate(rows, 2):
                value = row.get(text_field)
                process(str(index), value, text_label(row))
        elif filename.endswith((".jsonl", ".ndjson")):
            for index, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    label = text_label(row)
                    process(
                        str(row.get("sample_id", index)), row.get(text_field), label
                    )
                except (ValueError, AttributeError, TypeError) as error:
                    errors.append({"source": str(index), "message": str(error)[:300]})
        else:
            raise ValueError("文本导入支持 CSV 和 JSONL")
    elif filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (
                len(members) > 10000
                or sum(x.file_size for x in members) > settings().upload_max_bytes * 5
            ):
                raise ValueError("压缩包展开后超过限制")
            for member in members:
                path = PurePosixPath(member.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.filename
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise ValueError("压缩包含非法路径或符号链接")
                if member.file_size > 20 * 1024 * 1024 and path.suffix.lower() in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                ):
                    raise ValueError("单图片超过 20 MB")
            names = {x.filename for x in members}
            # 系统导出的 manifest 恢复固定标签映射；第三方数据可由导入配置提供。
            mapping = config.get("label_mapping", {})
            if "label_map.json" in names:
                mapping = json.loads(archive.read("label_map.json"))
            coco = None
            if "annotations.json" in names:
                coco = json.loads(archive.read("annotations.json"))
            label_names = {x["name"]: x["id"] for x in schema.labels} if schema else {}
            if coco and not mapping:
                mapping = {
                    str(x["id"]): label_names.get(x["name"])
                    for x in coco.get("categories", [])
                }
            if not mapping and "data.yaml" in names:
                import yaml

                yolo_names = yaml.safe_load(archive.read("data.yaml")).get("names", {})
                if isinstance(yolo_names, list):
                    yolo_names = dict(enumerate(yolo_names))
                mapping = {
                    str(i): label_names.get(name) for i, name in yolo_names.items()
                }
            if "manifest.json" in names and schema:
                manifest = json.loads(archive.read("manifest.json"))
                old_names = {x["id"]: x["name"] for x in manifest.get("labels", [])}
                mapping = {
                    key: label_names.get(old_names.get(value, ""), value)
                    for key, value in mapping.items()
                }
            for member in members:
                path = PurePosixPath(member.filename)
                if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                    continue
                check()
                try:
                    payload = archive.read(member)
                    label = None
                    if purpose != "none" and schema:
                        _, w, h, _, _ = image_data(payload)
                        boxes = []
                        if coco:
                            image_record = next(
                                (
                                    x
                                    for x in coco["images"]
                                    if x["file_name"] in (member.filename, path.name)
                                ),
                                None,
                            )
                            if not image_record:
                                raise ValueError("COCO 图片记录缺失")
                            for a in coco["annotations"]:
                                if a["image_id"] != image_record["id"]:
                                    continue
                                x, y, bw, bh = a["bbox"]
                                lid = mapping.get(str(a["category_id"]))
                                if not lid:
                                    raise ValueError("请提供 COCO 类别映射")
                                boxes.append(
                                    {"label_id": lid, "xyxy": [x, y, x + bw, y + bh]}
                                )
                        else:
                            label_path = str(
                                PurePosixPath("labels") / (path.stem + ".txt")
                            )
                            if label_path not in names and member.filename.startswith(
                                "images/"
                            ):
                                label_path = str(
                                    PurePosixPath(
                                        "labels/" + member.filename[len("images/") :]
                                    ).with_suffix(".txt")
                                )
                            if label_path not in names:
                                raise ValueError(
                                    "YOLO 标签文件缺失；无目标图片也需要空文件"
                                )
                            original = Image.open(io.BytesIO(payload))
                            w, h = original.size
                            original.close()
                            for line in archive.read(label_path).decode().splitlines():
                                if not line.strip():
                                    continue
                                cat, cx, cy, bw, bh = line.split()
                                cx, cy, bw, bh = map(float, (cx, cy, bw, bh))
                                lid = mapping.get(cat)
                                if not lid:
                                    raise ValueError("请提供 YOLO 类别映射")
                                boxes.append(
                                    {
                                        "label_id": lid,
                                        "xyxy": [
                                            (cx - bw / 2) * w,
                                            (cy - bh / 2) * h,
                                            (cx + bw / 2) * w,
                                            (cy + bh / 2) * h,
                                        ],
                                    }
                                )
                        # 原始图片的标签坐标与 EXIF 方向一起转换。
                        _, nw, nh, orientation, original_size = image_data(payload)
                        fake = type(
                            "ImageInfo",
                            (),
                            {
                                "metadata_json": {
                                    "orientation": orientation,
                                    "original_size": original_size,
                                },
                                "width": nw,
                                "height": nh,
                            },
                        )()
                        for box in boxes:
                            box["xyxy"] = transform_box(box["xyxy"], fake)
                        label = {
                            "task_type": project.task_type,
                            "coordinate_space": "original_image_pixels",
                            "explicit_empty": not boxes,
                            "boxes": boxes,
                        }
                    process(member.filename, payload, label)
                except (JobCancelled, LeaseLost):
                    raise
                except Exception as error:
                    errors.append(
                        {"source": member.filename, "message": str(error)[:300]}
                    )
    else:
        process(config["filename"], data)
    if not count and not errors:
        raise ValueError("文件中没有可导入数据")
    dataset.status = "needs_resolution" if errors else "ready"
    job.errors = errors
    job.result = {"dataset_id": dataset.id, "imported": count, "errors": len(errors)}
    job.status = "partially_failed" if errors else "succeeded"


def export_data(db, export, check=lambda: None):
    from .models import Item, Revision, LabelSchema

    schema = db.get(LabelSchema, export.schema_id)
    labels = {x["id"]: x["name"] for x in schema.labels}
    mapping = {str(i): x["id"] for i, x in enumerate(schema.labels)}
    manifest = {
        "schema_id": schema.id,
        "project_id": export.project_id,
        "snapshot": export.snapshot,
        "labels": schema.labels,
    }
    entries = []
    for entry in export.snapshot:
        check()
        item = db.get(Item, entry["item_id"])
        revision = db.get(Revision, entry["revision_id"])
        entries.append((item, revision, entry.get("split", "train")))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        archive.writestr("label_map.json", json.dumps(mapping, ensure_ascii=False))
        if export.format in ("jsonl", "csv"):
            rows = [
                {
                    "sample_id": item.id,
                    "text": item.text,
                    "label_id": revision.content["label_id"],
                    "label_name": labels[revision.content["label_id"]],
                    "revision_id": revision.id,
                    "split": split,
                }
                for item, revision, split in entries
            ]
            if export.format == "jsonl":
                archive.writestr(
                    "annotations.jsonl",
                    "\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
                )
            else:
                text = io.StringIO()
                writer = csv.DictWriter(
                    text,
                    fieldnames=[
                        "sample_id",
                        "text",
                        "label_id",
                        "label_name",
                        "revision_id",
                        "split",
                    ],
                )
                writer.writeheader()
                writer.writerows(rows)
                archive.writestr("annotations.csv", text.getvalue())
        else:
            categories = [
                {"id": i, "name": x["name"]} for i, x in enumerate(schema.labels)
            ]
            image_records = []
            annotations = []
            class_ids = {value: int(key) for key, value in mapping.items()}
            for index, (item, revision, split) in enumerate(entries):
                check()
                filename = f"{item.id}.png"
                archive.writestr(
                    f"images/{split}/{filename}", storage.read(item.object_key)
                )
                image_records.append(
                    {
                        "id": index,
                        "file_name": f"images/{split}/{filename}",
                        "width": item.width,
                        "height": item.height,
                    }
                )
                lines = []
                for box in revision.content["boxes"]:
                    x1, y1, x2, y2 = box["xyxy"]
                    cat = class_ids[box["label_id"]]
                    lines.append(
                        f"{cat} {(x1 + x2) / 2 / item.width:.8f} {(y1 + y2) / 2 / item.height:.8f} {(x2 - x1) / item.width:.8f} {(y2 - y1) / item.height:.8f}"
                    )
                    annotations.append(
                        {
                            "id": len(annotations),
                            "image_id": index,
                            "category_id": cat,
                            "bbox": [x1, y1, x2 - x1, y2 - y1],
                            "area": (x2 - x1) * (y2 - y1),
                            "iscrowd": 0,
                        }
                    )
                archive.writestr(f"labels/{item.id}.txt", "\n".join(lines))
                archive.writestr(f"labels/{split}/{item.id}.txt", "\n".join(lines))
            if export.format == "coco":
                archive.writestr(
                    "annotations.json",
                    json.dumps(
                        {
                            "images": image_records,
                            "annotations": annotations,
                            "categories": categories,
                        },
                        ensure_ascii=False,
                    ),
                )
            import yaml

            archive.writestr(
                "data.yaml",
                yaml.safe_dump(
                    {
                        "path": ".",
                        "train": "images/train",
                        "val": "images/val",
                        "test": "images/test",
                        "names": {i: x["name"] for i, x in enumerate(schema.labels)},
                    },
                    allow_unicode=True,
                ),
            )
    export.object_key = storage.put(output.getvalue(), "zip")
    return {
        "export_id": export.id,
        "count": len(entries),
        "object_key": export.object_key,
    }
