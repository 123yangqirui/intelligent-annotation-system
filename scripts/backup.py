"""本机业务备份；恢复脚本只创建新的隔离数据库与文件目录。"""

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import tarfile
import shutil

parser = argparse.ArgumentParser()
parser.add_argument("destination")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
destination = Path(args.destination).resolve() / datetime.now().strftime(
    "%Y%m%d-%H%M%S"
)
destination.mkdir(parents=True, exist_ok=False, mode=0o700)
subprocess.run(
    [
        "/opt/homebrew/opt/postgresql@17/bin/pg_dump",
        "-h",
        "127.0.0.1",
        "-p",
        "55432",
        "-U",
        "annotation",
        "-d",
        "annotation_db",
        "-Fc",
        "-f",
        str(destination / "database.dump"),
    ],
    check=True,
)
with tarfile.open(destination / "objects.tar.gz", "w:gz") as archive:
    if (root / "backend" / "data").exists():
        archive.add(root / "backend" / "data", arcname="data")
if (root / "backend" / ".env").exists():
    shutil.copy2(root / "backend" / ".env", destination / "secrets.env")
    (destination / "secrets.env").chmod(0o600)
mongodump = shutil.which("mongodump")
if mongodump:
    subprocess.run(
        [
            mongodump,
            "--uri=mongodb://127.0.0.1:57017/annotation_events",
            "--archive=" + str(destination / "mongo.archive"),
            "--gzip",
        ],
        check=True,
    )
print("备份已保存：", destination)
