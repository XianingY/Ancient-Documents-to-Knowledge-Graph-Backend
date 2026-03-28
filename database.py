"""
数据库初始化模块
"""
import os
from enum import Enum
from datetime import datetime, timezone, timedelta
from sqlite3 import Connection as SQLite3Connection
from sqlalchemy import create_engine, Integer, String, DateTime, ForeignKey, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, mapped_column, Mapped
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.core.config import settings

# 数据库路径
DB_DIR = "database"
DB_NAME = "app.db"
DB_PATH = os.path.join(DB_DIR, DB_NAME)

if settings.DATABASE_URL:
    DATABASE_URL = settings.DATABASE_URL
else:
    DATABASE_URL = f"sqlite:///{DB_PATH}"

# 创建数据库目录 (仅当使用本地 SQLite 时)
if DATABASE_URL.startswith("sqlite:///") and DB_PATH in DATABASE_URL:
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)

# 创建SQLAlchemy引擎和会话
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """为 SQLite 显式开启外键约束。"""
    if isinstance(dbapi_connection, SQLite3Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()
    elif DATABASE_URL.startswith("mysql") or DATABASE_URL.startswith("postgresql"):
        pass # MySQL and PostgreSQL enforce foreign keys natively by default when set up correctly.

# 获取北京时间（UTC+8）
def get_beijing_time():
    """获取北京时间（东八区）"""
    tz_beijing = timezone(timedelta(hours=8))
    return datetime.now(tz_beijing)

# 基类
Base = declarative_base()


class OcrStatus(str, Enum):
    """状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class User(Base):
    """用户表"""
    __tablename__ = "user"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, nullable=True)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    images = relationship("Image", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)


class Image(Base):
    """图像表"""
    __tablename__ = "image"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    upload_time: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    user = relationship("User", back_populates="images")
    ocr_results = relationship("OcrResult", back_populates="image", cascade="all, delete-orphan", passive_deletes=True)


class OcrResult(Base):
    """OCR结果表"""
    __tablename__ = "ocr_result"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    image_id: Mapped[int] = mapped_column(Integer, ForeignKey("image.id", ondelete="CASCADE"), index=True)
    raw_text: Mapped[str] = mapped_column(String)
    status: Mapped[OcrStatus] = mapped_column(SQLEnum(OcrStatus), default=OcrStatus.PROCESSING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    image = relationship("Image", back_populates="ocr_results")
    structured_results = relationship("StructuredResult", back_populates="ocr_result", cascade="all, delete-orphan", passive_deletes=True)


class StructuredResult(Base):
    """结构化结果表"""
    __tablename__ = "structured_result"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ocr_result_id: Mapped[int] = mapped_column(Integer, ForeignKey("ocr_result.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(String)  # JSON格式
    status: Mapped[OcrStatus] = mapped_column(SQLEnum(OcrStatus), default=OcrStatus.PROCESSING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    ocr_result = relationship("OcrResult", back_populates="structured_results")
    relation_graphs = relationship("RelationGraph", back_populates="structured_result", cascade="all, delete-orphan", passive_deletes=True)
    multi_task_associations = relationship("MultiTaskStructuredResult", back_populates="structured_result", cascade="all, delete-orphan", passive_deletes=True)


class RelationGraph(Base):
    """关系图表"""
    __tablename__ = "relation_graph"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    structured_result_id: Mapped[int] = mapped_column(Integer, ForeignKey("structured_result.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(String)  # JSON格式
    status: Mapped[OcrStatus] = mapped_column(SQLEnum(OcrStatus), default=OcrStatus.PROCESSING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    structured_result = relationship("StructuredResult", back_populates="relation_graphs")


class MultiTask(Base):
    """跨文档分析任务表"""
    __tablename__ = "multi_task"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    status: Mapped[OcrStatus] = mapped_column(SQLEnum(OcrStatus), default=OcrStatus.PROCESSING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    user = relationship("User")
    multi_relation_graphs = relationship("MultiRelationGraph", back_populates="multi_task", cascade="all, delete-orphan", passive_deletes=True)
    structured_result_associations = relationship("MultiTaskStructuredResult", back_populates="multi_task", cascade="all, delete-orphan", passive_deletes=True)


class MultiRelationGraph(Base):
    """跨文档关系图表"""
    __tablename__ = "multi_relation_graph"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    multi_task_id: Mapped[int] = mapped_column(Integer, ForeignKey("multi_task.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(String)  # JSON格式
    status: Mapped[OcrStatus] = mapped_column(SQLEnum(OcrStatus), default=OcrStatus.PROCESSING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    multi_task = relationship("MultiTask", back_populates="multi_relation_graphs")


class MultiTaskStructuredResult(Base):
    """多任务与结构化结果关系表"""
    __tablename__ = "multi_task_structured_result"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    multi_task_id: Mapped[int] = mapped_column(Integer, ForeignKey("multi_task.id", ondelete="CASCADE"), index=True)
    structured_result_id: Mapped[int] = mapped_column(Integer, ForeignKey("structured_result.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_time)
    
    # 关系
    multi_task = relationship("MultiTask", back_populates="structured_result_associations")
    structured_result = relationship("StructuredResult", back_populates="multi_task_associations")
    
    __table_args__ = (
        # 联合唯一约束
        UniqueConstraint("multi_task_id", "structured_result_id"),
    )


def init_db():
    """
    初始化数据库
    检查数据库是否存在，如果不存在则创建所有表
    """
    db_exists = os.path.exists(DB_PATH)
    
    # 创建所有表
    Base.metadata.create_all(bind=engine)
    
    if not db_exists:
        print(f"数据库已创建: {DB_PATH}")
    else:
        print(f"数据库已存在: {DB_PATH}")


def get_db():
    """
    获取数据库会话
    用于依赖注入
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
