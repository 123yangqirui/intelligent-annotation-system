"""可重复执行的功能演示数据；合成图片不作为准确率评估数据。"""

import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from sqlalchemy import select
from PIL import Image, ImageDraw
from app.db import session_scope
from app.models import User, Project, Member, LabelSchema, Dataset, uid
from app.imports import add_item
from app.annotations import get_work

with session_scope() as db:
    admin = db.scalar(select(User).where(User.username == "admin"))
    if not admin:
        raise SystemExit("请先初始化管理员。")
    for kind, name, names in [
        (
            "text_classification",
            "客服分类 · 示例",
            ["退款", "物流", "商品质量", "其他"],
        ),
        ("image_detection", "图像工作台 · 合成示例", ["盒子", "标签"]),
    ]:
        if db.scalar(
            select(Project).where(Project.name == name, Project.created_by == admin.id)
        ):
            continue
        project = Project(
            name=name,
            task_type=kind,
            description="用于练习标注、审核和导出。个人练习项目允许本人审核。",
            created_by=admin.id,
            allow_self_review=True,
        )
        db.add(project)
        db.flush()
        db.add(Member(project_id=project.id, user_id=admin.id, role="owner"))
        schema = LabelSchema(
            project_id=project.id,
            version=1,
            status="published",
            labels=[
                {
                    "id": uid(),
                    "name": label,
                    "description": "按主要对象或主要诉求判断",
                    "color": color,
                }
                for label, color in zip(
                    names, ["#4268e8", "#2d9870", "#d69a39", "#875bd2"]
                )
            ],
            rules={
                "instructions": "文本按主要诉求分类；图片标出完整对象，漏检需要补框。"
            },
        )
        db.add(schema)
        db.flush()
        project.active_schema_id = schema.id
        dataset = Dataset(
            project_id=project.id,
            name="功能演示数据",
            status="staging",
            config={"source": "synthetic demonstration only"},
        )
        db.add(dataset)
        db.flush()
        if kind == "text_classification":
            values = [
                "订单已经取消，希望退还付款。",
                "包裹三天没有更新物流，请帮忙查询。",
                "收到的杯子有裂痕，想申请换货。",
                "请问支持开发票吗？",
                "退款已经申请一周，还没有到账。",
            ]
            for i, value in enumerate(values):
                item = add_item(db, project, dataset, value, f"demo-text-{i + 1}")
                get_work(db, item.id, schema.id)
        else:
            image = Image.new("RGB", (900, 560), "#f2f0eb")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (120, 125, 380, 400),
                radius=12,
                fill="#bd935a",
                outline="#815c36",
                width=3,
            )
            draw.rectangle((205, 205, 310, 270), fill="white")
            draw.rounded_rectangle(
                (480, 170, 750, 430),
                radius=12,
                fill="#99b9ac",
                outline="#587c6e",
                width=3,
            )
            output = io.BytesIO()
            image.save(output, format="PNG")
            item = add_item(
                db, project, dataset, output.getvalue(), "synthetic-boxes.png"
            )
            get_work(db, item.id, schema.id)
        dataset.status = "ready"
print("示例项目已准备。未生成模拟模型预测。")
