"""数据库初始化模块，使用 SQLModel + SQLite。"""

from sqlmodel import SQLModel, create_engine, Session
from app.config import settings


def get_engine():
    """创建并返回 SQLite 数据库引擎。"""
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_path}"
    return create_engine(db_url, echo=settings.debug, connect_args={"check_same_thread": False})


engine = get_engine()


def init_db() -> None:
    """初始化数据库，创建所有表。"""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI 依赖注入用的数据库会话生成器。"""
    with Session(engine) as session:
        yield session
