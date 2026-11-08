import hashlib
import json
import math
from sqlalchemy import select
from .models import Work, Revision, uid
from .security import fail


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def validate_labels(labels):
    if not labels or len(labels) > 500:
        fail("INVALID_LABELS", "标签数量须在 1–500 之间。")
    result = []
    ids, names = set(), set()
    for label in labels:
        name = str(label.get("name", "")).strip()
        label_id = label.get("id") or uid()
        from uuid import UUID

        try:
            UUID(label_id)
        except (ValueError, TypeError, AttributeError):
            fail("INVALID_LABELS", "标签 ID 必须为 UUID。")
        if not name or len(name) > 100 or name in names or label_id in ids:
            fail("INVALID_LABELS", "标签名称和 ID 必须非空且唯一。")
        names.add(name)
        ids.add(label_id)
        result.append(
            {
                "id": label_id,
                "name": name,
                "description": str(label.get("description", "")),
                "color": label.get("color", "#4268e8"),
            }
        )
    return result


def validate_content(project, schema, item, content):
    if (
        schema.project_id != project.id
        or item.project_id != project.id
        or schema.status != "published"
    ):
        fail("SCHEMA_MISMATCH", "数据与已发布规范必须属于同一项目。")
    labels = {x["id"] for x in schema.labels}
    if content.get("task_type") != project.task_type:
        fail("TASK_MISMATCH", "标注任务类型不匹配。")
    if project.task_type == "text_classification":
        if content.get("label_id") not in labels:
            fail("INVALID_LABEL", "请选择规范中的有效标签。")
        return {"task_type": project.task_type, "label_id": content["label_id"]}
    if content.get("coordinate_space") != "original_image_pixels":
        fail("INVALID_BOX", "检测框须使用原图像素坐标。")
    boxes = content.get("boxes")
    if not isinstance(boxes, list) or len(boxes) > 5000:
        fail("INVALID_BOX", "目标框列表无效。")
    empty = content.get("explicit_empty") is True
    if not boxes and not empty:
        fail("EMPTY_UNCONFIRMED", "请明确确认图片中无目标。")
    if boxes and empty:
        fail("INVALID_BOX", "有目标框时不能声明无目标。")
    normalized, seen = [], set()
    for box in boxes:
        if not isinstance(box, dict):
            fail("INVALID_BOX", "每个目标框必须是对象。")
        coords = box.get("xyxy", [])
        if (
            not isinstance(coords, list)
            or len(coords) != 4
            or any(
                not isinstance(x, (int, float))
                or isinstance(x, bool)
                or not math.isfinite(x)
                for x in coords
            )
        ):
            fail("INVALID_BOX", "坐标必须是四个有限数字。")
        x1, y1, x2, y2 = coords
        if (
            not (0 <= x1 < x2 <= item.width and 0 <= y1 < y2 <= item.height)
            or box.get("label_id") not in labels
        ):
            fail("INVALID_BOX", "框越界、面积为零或类别无效。")
        object_id = box.get("object_id") or uid()
        if not isinstance(object_id, str) or len(object_id) > 100 or object_id in seen:
            fail("INVALID_BOX", "目标框 ID 重复。")
        seen.add(object_id)
        normalized.append(
            {"object_id": object_id, "label_id": box["label_id"], "xyxy": list(coords)}
        )
    return {
        "task_type": project.task_type,
        "coordinate_space": "original_image_pixels",
        "explicit_empty": empty,
        "boxes": normalized,
    }


def get_work(db, item_id, schema_id, lock=False):
    query = select(Work).where(Work.item_id == item_id, Work.schema_id == schema_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = db.scalar(query)
    if not result:
        result = Work(item_id=item_id, schema_id=schema_id)
        db.add(result)
        db.flush()
    return result


def same_operation(db, table, operation_id, request_hash, actor_id):
    old = db.scalar(select(table).where(table.operation_id == operation_id))
    if old:
        owner = old.actor_id if isinstance(old, Revision) else old.reviewer_id
        if old.request_hash != request_hash or owner != actor_id:
            fail("IDEMPOTENCY_CONFLICT", "重复操作的内容或操作者不一致。", 409)
    return old
