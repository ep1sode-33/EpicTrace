"""把 eval-data 入库到一个 eval Project 并建索引。真重活,懒导入,手动跑(无单测,需真模型)。"""
from __future__ import annotations

from pathlib import Path


def index_eval_corpus(eval_data_dir: str | Path, *, project_name: str = "rag-eval") -> int:
    """把 eval_data_dir 下每个文件入库到一个 eval Project,再跑真索引(真 embedding)。

    返回 project_id。重依赖(FlagEmbedding / Milvus)全部懒导入在函数内,
    确保测试套件 import 本模块时不拉重依赖。
    """
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.services.index import IndexService
    from epictrace.services.ingest import IngestService
    from epictrace.services.projects import ProjectService
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    cfg = AppConfig()
    db = Database(cfg)
    # 指向真实 data_dir;首跑该库可能不存在/缺表,create_all 幂等(只补缺表/缺列)。
    db.create_all()

    # eval Project 需要一个真实可写文件夹(ingest 会把文件拷进去)。
    folder = Path(cfg.data_dir) / "projects" / project_name
    folder.mkdir(parents=True, exist_ok=True)
    proj = ProjectService(db).create(title=project_name, folder_path=str(folder))

    ing = IngestService(db)
    for f in sorted(Path(eval_data_dir).glob("*")):
        if f.is_file() and f.name != "manifest.jsonl":
            ing.ingest_file(proj.id, str(f), ingest_method="rag_eval", description="")

    # vector_store 用 getter(lambda):把 Milvus(gRPC)构造推迟到 embedder warmup 之后,
    # 避免 macOS fork 段错误(IndexService._run 内部保证此顺序)。
    svc = IndexService(db, BgeM3Embedder(), lambda: MilvusLiteStore(db_path=cfg.milvus_path))
    job = svc.index_project(proj.id)
    svc.run_in_background(job).join()   # join 等后台线程跑完,转成同步完成
    return proj.id
