from sqlalchemy import select
from ..models import AgentSkill
from ..annotations import digest

BUILTINS = [
    (
        "text-preannotation",
        "文本分类预标注",
        "检查文本项目、已发布标签规范和数据版本。查询兼容分类模型。无法确定版本或模型时请求补充。创建预测任务；根据真实任务结果报告成功、失败和人工复核要求。",
    ),
    (
        "image-preannotation",
        "图像检测预标注",
        "检查图像检测项目、原图坐标规范和模型类别能力。使用已注册检测模型创建预测任务。空预测不代表人工确认无目标。报告低分和空预测等复核原因。",
    ),
    (
        "reviewed-export",
        "已审核成果导出",
        "仅在用户允许 export 操作时计划导出。选择文本 JSONL/CSV 或图像 YOLO/COCO。只导出人工审核快照；没有已审核数据时明确报告原因。模型预测不能自动取得人工审核状态。",
    ),
    (
        "quality-summary",
        "标注质量汇总",
        "根据数据库任务计数与预测复核信号汇总。区分排队、执行、完成和部分失败。不把模型分数描述成已校准正确概率，不编造标注数量和准确率。",
    ),
]


def seed_skills(db):
    added = 0
    for name, description, content in BUILTINS:
        if db.scalar(select(AgentSkill).where(AgentSkill.name == name)):
            continue
        db.add(AgentSkill(name=name, description=description, content=content))
        added += 1
    db.flush()
    return added


def snapshot_skills(db, ids):
    result = []
    for skill_id in dict.fromkeys(ids):
        skill = db.get(AgentSkill, skill_id)
        if not skill or not skill.enabled:
            raise ValueError("技能不存在或已停用")
        result.append(
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "content": skill.content,
                "version": skill.version,
                "hash": digest(skill.content),
            }
        )
    if sum(len(x["content"].encode()) for x in result) > 32000:
        raise ValueError("所选技能合计超过 32 KB")
    return result
