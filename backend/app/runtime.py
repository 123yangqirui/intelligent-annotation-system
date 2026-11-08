"""部署控制入口；只允许固定模板和参数，API 不调用 Docker。"""

from pathlib import Path
import re
import shutil
import subprocess
import time
import httpx
from .models import Deployment
from .security import fail
from .config import settings

ROOT = settings().model_root
TEMPLATES = {
    "bert": ("annotation-text", "text_classification"),
    "yolo": ("annotation-image", "image_detection"),
    "qwen": ("vllm/vllm-openai:v0.11.0", "text_classification"),
    "deepseek": ("vllm/vllm-openai:v0.11.0", "text_classification"),
}


def validate_deployment(config, model):
    template = config["template"]
    if template not in TEMPLATES or TEMPLATES[template][1] != model.task_type:
        fail("INVALID_TEMPLATE", "模板和模型任务不匹配。")
    supported = {
        "bert": ("bert", "http"),
        "yolo": ("yolo", "http"),
        "qwen": ("llm",),
        "deepseek": ("llm",),
    }
    if model.adapter not in supported[template]:
        fail("INVALID_TEMPLATE", "部署模板与模型适配器不匹配。")
    if config["checkpoint"].startswith("trained:"):
        artifact = Path(model.config.get("artifact_path", "")).resolve()
        if (
            model.config.get("checkpoint") != config["checkpoint"]
            or not artifact.is_relative_to(
                (settings().storage_root / "training").resolve()
            )
            or not artifact.exists()
        ):
            fail("INVALID_CHECKPOINT", "训练模型引用无效。")
    elif not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?",
        config["checkpoint"],
    ):
        fail("INVALID_CHECKPOINT", "检查点须为受支持模型仓库名称。")
    if not 8101 <= config["port"] <= 8199:
        fail("INVALID_PORT", "模型端口超出支持范围。")


def run_deployment(db, job):
    deployment = db.get(Deployment, job.config["deployment_id"])
    if not shutil.which("docker"):
        deployment.status = "failed"
        deployment.error = "Docker 不可用；请先配置 Docker 和所需 GPU 运行环境。"
        db.commit()
        raise RuntimeError(deployment.error)
    name = "annotation-model-" + deployment.id
    if job.type == "stop_deployment":
        deployment.status = "draining"
        db.commit()
        subprocess.run(
            ["docker", "stop", "--time", "30", name],
            check=True,
            capture_output=True,
            timeout=45,
        )
        deployment.status = "stopped"
        deployment.error = None
        return {"deployment_id": deployment.id, "status": "stopped"}
    config = deployment.config
    template = config["template"]
    image = TEMPLATES[template][0]
    deployment.status = "preparing"
    db.commit()
    try:
        if template in ("bert", "yolo"):
            service = "text" if template == "bert" else "image"
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    image,
                    str(ROOT / "model_services" / service),
                ],
                check=True,
                capture_output=True,
                timeout=1800,
            )
        elif subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True
        ).returncode:
            subprocess.run(
                ["docker", "pull", image], check=True, capture_output=True, timeout=1800
            )
        present = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
        )
        port = config["port"]
        deployment.status = "starting"
        db.commit()
        if present.returncode == 0:
            if present.stdout.strip() != "true":
                subprocess.run(
                    ["docker", "start", name],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
        else:
            command = [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-p",
                f"127.0.0.1:{port}:8000",
            ]
            if settings().model_docker_network:
                command += ["--network", settings().model_docker_network]
            if template in ("bert", "yolo"):
                checkpoint = config["checkpoint"]
                if checkpoint.startswith("trained:"):
                    from .models import ModelSpec

                    model = db.get(ModelSpec, deployment.model_id)
                    artifact = Path(model.config["artifact_path"])
                    target = (
                        "/trained-model.pt" if artifact.is_file() else "/trained-model"
                    )
                    command += ["-v", f"{artifact.resolve()}:{target}:ro"]
                    checkpoint = target
                command += ["-e", "CHECKPOINT=" + checkpoint, image]
            else:
                command += [
                    "--gpus",
                    "all",
                    image,
                    "--model",
                    config["checkpoint"],
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ]
            subprocess.run(command, check=True, capture_output=True, timeout=60)
        deployment.runtime_id = name
        deployment.status = "warming"
        db.commit()
        for _ in range(180):
            try:
                health_host = (
                    f"{name}:8000"
                    if settings().model_docker_network
                    else f"127.0.0.1:{port}"
                )
                if httpx.get(f"http://{health_host}/health", timeout=2).is_success:
                    deployment.status = "ready"
                    deployment.error = None
                    return {
                        "deployment_id": deployment.id,
                        "endpoint": f"http://127.0.0.1:{port}",
                    }
            except httpx.TransportError:
                pass
            time.sleep(2)
        raise RuntimeError("服务加载超时，请查看 Docker 模型日志与显存。")
    except Exception as error:
        deployment.status = "failed"
        deployment.error = str(error)[:500]
        db.commit()
        raise
