import io, json, zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.db import session_scope
from app import storage
from app.imports import transform_box, image_data
from app.models import Reference, Project, Item, Revision, Dataset, Work
from sqlalchemy import select
from test_workflow import image_bytes


@pytest.mark.parametrize(
    "key", ["../secret", "/etc/passwd", "objects/../../secret", "objects\\secret"]
)
def test_storage_rejects_traversal(env, key):
    with pytest.raises(ValueError):
        storage.read(key)


def test_storage_concurrent_writes_and_symlink_escape(env, tmp_path):
    from app.config import settings

    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(storage.put, [b"same-content"] * 12))
    assert len(set(keys)) == 1 and storage.read(keys[0]) == b"same-content"
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    (settings().storage_root / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        storage.read("link")


def test_s3_adapter_contract(env, monkeypatch):
    import boto3
    from app.config import settings

    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    settings.cache_clear()
    objects = {}

    class S3:
        def put_object(self, **kw):
            objects[kw["Key"]] = kw["Body"]
            assert kw["Bucket"] == "test-bucket"

        def get_object(self, **kw):
            return {"Body": io.BytesIO(objects[kw["Key"]])}

    monkeypatch.setattr(boto3, "client", lambda *a, **kw: S3())
    key = storage.put(b"example")
    assert storage.read(key) == b"example"


@pytest.mark.parametrize(
    "orientation,expected",
    [
        (1, [10, 20, 30, 40]),
        (3, [70, 40, 90, 60]),
        (6, [40, 10, 60, 30]),
        (8, [20, 70, 40, 90]),
    ],
)
def test_exif_box_conversion(orientation, expected):
    item = SimpleNamespace(
        width=100,
        height=80,
        metadata_json={"orientation": orientation, "original_size": [100, 80]},
    )
    assert transform_box([10, 20, 30, 40], item) == expected


def test_csv_partial_failure_and_manual_resolution(client, workspace):
    from app.tasks import execute_job

    project, _, _, headers = workspace()
    response = client.post(
        "/api/v1/projects/" + project["id"] + "/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("sample.csv", b'text\ngood row\n""\n')},
    )
    execute_job.run(response.json()["id"])
    job = client.get("/api/v1/jobs/" + response.json()["id"], headers=headers).json()
    assert (
        job["status"] == "partially_failed"
        and job["result"]["imported"] == 1
        and job["result"]["errors"] == 1
    )
    assert (
        client.post(
            "/api/v1/datasets/" + job["result"]["dataset_id"] + "/publish",
            headers=headers,
        ).status_code
        == 200
    )


def test_image_pixel_limit(env, monkeypatch):
    from app.config import settings

    monkeypatch.setenv("IMAGE_MAX_PIXELS", "100")
    settings.cache_clear()
    with pytest.raises(ValueError, match="像素"):
        image_data(image_bytes())


def test_coco_import_maps_names_and_hides_reference(client, workspace):
    from app.tasks import execute_job

    project, schema, _, headers = workspace("image_detection")
    binary = io.BytesIO()
    with zipfile.ZipFile(binary, "w") as archive:
        archive.writestr("images/example.png", image_bytes())
        archive.writestr(
            "annotations.json",
            json.dumps(
                {
                    "images": [{"id": 1, "file_name": "images/example.png"}],
                    "categories": [{"id": 17, "name": "A"}],
                    "annotations": [
                        {
                            "id": 1,
                            "image_id": 1,
                            "category_id": 17,
                            "bbox": [1, 2, 30, 40],
                        }
                    ],
                }
            ),
        )
    response = client.post(
        "/api/v1/projects/" + project["id"] + "/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"file": ("coco.zip", binary.getvalue())},
        data={"labels_purpose": "evaluation", "schema_id": schema["id"]},
    )
    execute_job.run(response.json()["id"])
    job = client.get("/api/v1/jobs/" + response.json()["id"], headers=headers).json()
    assert job["status"] == "succeeded", job
    with session_scope() as db:
        ref = db.scalar(select(Reference))
        assert ref.content["boxes"][0]["xyxy"] == [1, 2, 31, 42]
        item_id = ref.item_id
    item = client.get("/api/v1/items/" + item_id, headers=headers).json()
    assert not item["revisions"] and "reference" not in item
