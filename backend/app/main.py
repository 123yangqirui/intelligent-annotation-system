"""FastAPI application, request correlation and consistent error responses."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from typing import Literal
from uuid import uuid4
import logging

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi import Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError
from app.api import router
from app.agent.api import router as agent_router

logger = logging.getLogger("annotation")


class HealthResponse(BaseModel):
    """健康检查的响应结构：{
        "code": 200,
        "status": "ok"
    }"""

    code: int = 200
    status: Literal["ok"] = "ok"


# 创建一个单例app
app = FastAPI(
    title="智能标注系统 API",
    description="文本与图像标注系统：身份、项目、标注、审核、导出、模型与后台任务。",
    version="0.2.0",
)
app.include_router(router)
app.include_router(agent_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    fields = [
        {"field": ".".join(map(str, x["loc"])), "message": x["msg"], "type": x["type"]}
        for x in error.errors()
    ]
    return JSONResponse(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": fields[0]["message"] if fields else "请求参数无效",
                "fields": fields,
                "request_id": getattr(request.state, "request_id", ""),
            }
        },
        status_code=422,
    )


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    detail = (
        error.detail
        if isinstance(error.detail, dict)
        else {"code": "HTTP_ERROR", "message": str(error.detail)}
    )
    return JSONResponse(
        {"error": {**detail, "request_id": getattr(request.state, "request_id", "")}},
        status_code=error.status_code,
        headers=error.headers,
    )


@app.exception_handler(StaleDataError)
async def stale_error(request: Request, error):
    return JSONResponse(
        {"error": {"code": "REVISION_CONFLICT", "message": "数据已更新，请重新加载。"}},
        status_code=409,
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, error):
    return JSONResponse(
        {"error": {"code": "DATA_CONFLICT", "message": "数据重复或关联约束冲突。"}},
        status_code=409,
    )


@app.exception_handler(OperationalError)
async def database_error(request: Request, error):
    logger.error(
        "database_unavailable request_id=%s", getattr(request.state, "request_id", "")
    )
    return JSONResponse(
        {
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "数据库不可用，请检查服务。",
            }
        },
        status_code=503,
    )


# 健康检查路由
@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["健康检查"],
    summary="健康检查",
)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/health/live", include_in_schema=False)
async def live():
    return {"status": "ok"}


if __name__ == "__main__":
    # 启动本地开发服务
    backend_dir = Path(__file__).resolve().parent.parent
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        app_dir=str(backend_dir),
        reload_dirs=[str(backend_dir)],
    )
