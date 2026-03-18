
import os
import shutil
import uuid
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, APIRouter, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, List
from database import (
    SessionLocal, init_db, get_db, Image, User, OcrResult, 
    StructuredResult, RelationGraph, MultiTask, MultiRelationGraph, 
    MultiTaskStructuredResult, get_beijing_time
)
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from dotenv import load_dotenv
import warnings
from PIL import Image as PILImage, UnidentifiedImageError

from app.services.ocr_service import ocr_image_by_id
from app.routers.multi_tasks import router as multi_task_router_v2
from app.worker.tasks import task_ocr_image, task_analyze_ocr_result, task_analyze_structured_result, task_analyze_multi_task

# 加载.env
load_dotenv()

# JWT 配置
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    warnings.warn(
        "未在环境变量中找到SECRET_KEY",
        UserWarning,
        stacklevel=2
    )
    SECRET_KEY = "temp"

ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 24 * 60 # 24h

# 密码加密
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer scheme
security = HTTPBearer()
# Optional HTTP Bearer scheme（用于不方便设置请求头的场景，如 img src）
security_optional = HTTPBearer(auto_error=False)

app = FastAPI()

# Pydantic 模型
class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime

class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None

class CreateStructuredResultRequest(BaseModel):
    ocr_result_id: int

class CreateRelationGraphRequest(BaseModel):
    structured_result_id: int

class CreateMultiTaskRequest(BaseModel):
    structured_result_ids: List[int]

class CreateMultiTaskByImagesRequest(BaseModel):
    image_ids: List[int]

class CreateMultiRelationGraphRequest(BaseModel):
    multi_task_id: int

class ChatQueryRequest(BaseModel):
    question: str

# 工具函数
def hash_password(password: str) -> str:
    """加密密码"""
    return pwd_context.hash(password[:72])

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password[:72], hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = get_beijing_time() + expires_delta
    else:
        expire = get_beijing_time() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, str(SECRET_KEY), algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> dict:
    """验证JWT token"""
    try:
        payload = jwt.decode(token, str(SECRET_KEY), algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token无效")


def build_thumbnail_path(filename: str) -> str:
    """根据原图文件名构造缩略图文件路径。"""
    stem, _ = os.path.splitext(filename)
    return os.path.join(THUMBNAIL_DIR, f"{stem}_thumb.jpg")


def ensure_thumbnail_exists(image_path: str, thumbnail_path: str, size: tuple[int, int] = (320, 320)) -> None:
    """按需生成缩略图并持久化到磁盘。"""
    if os.path.exists(thumbnail_path):
        return

    try:
        with PILImage.open(image_path) as img:
            rgb_img = img.convert("RGB")
            rgb_img.thumbnail(size)
            rgb_img.save(thumbnail_path, format="JPEG", quality=85, optimize=True)
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="无法识别的图片格式")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成缩略图失败: {str(e)}")

async def run_auto_image_pipeline(image_id: int) -> None:
    """后台串行执行 OCR -> 结构化提取 -> 关系图生成。"""
    db = SessionLocal()
    try:
        from app.services.analysis_service import analyze_ocr_result, analyze_structured_result

        # 记录调用前该图片最新 OCR id，便于定位本次新建记录
        latest_before = (
            db.query(OcrResult.id)
            .filter(OcrResult.image_id == image_id)
            .order_by(OcrResult.id.desc())
            .first()
        )
        latest_before_id = latest_before[0] if latest_before else 0

        ocr_success = await ocr_image_by_id(image_id, db)
        if not ocr_success:
            return

        ocr_result = (
            db.query(OcrResult)
            .filter(OcrResult.image_id == image_id, OcrResult.id > latest_before_id)
            .order_by(OcrResult.id.asc())
            .first()
        )
        if not ocr_result:
            return

        await analyze_ocr_result(ocr_result.id, db)

        structured_result = (
            db.query(StructuredResult)
            .filter(StructuredResult.ocr_result_id == ocr_result.id)
            .order_by(StructuredResult.id.desc())
            .first()
        )
        if not structured_result:
            return

        await analyze_structured_result(structured_result.id, db)
    except Exception as e:
        print(f"后台自动处理失败(image_id={image_id}): {str(e)}")
    finally:
        db.close()

# 数据库初始化
init_db()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 存储
UPLOAD_DIR = "pic"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

THUMBNAIL_DIR = os.path.join(UPLOAD_DIR, "thumbnails")
if not os.path.exists(THUMBNAIL_DIR):
    os.makedirs(THUMBNAIL_DIR)


# 路由分组

# 认证路由
auth_router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

# 用户路由
users_router = APIRouter(prefix="/api/v1/users", tags=["用户管理"])

# 图片路由
images_router = APIRouter(prefix="/api/v1/images", tags=["图片管理"])

# OCR路由
ocr_router = APIRouter(prefix="/api/v1/ocr-results", tags=["OCR结果"])

# 结构化结果路由
structured_result_router = APIRouter(prefix="/api/v1/structured-results", tags=["结构化结果"])

# 关系图路由
relation_graph_router = APIRouter(prefix="/api/v1/relation-graphs", tags=["关系图"])

# 多任务路由
# multi_task_router = APIRouter(prefix="/api/v1/multi-tasks", tags=["多任务分析"])

# 跨文档关系图路由
multi_relation_graph_router = APIRouter(prefix="/api/v1/multi-relation-graphs", tags=["跨文档关系图"])

# 智能问答路由
chat_router = APIRouter(prefix="/api/v1/chat", tags=["智能问答"])


# 认证路由

# POST /api/v1/auth/register - 注册
@auth_router.post("/register")
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    # 检查用户是否已存在
    existing_user = db.query(User).filter(User.username == request.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    # 创建新用户
    hashed_password = hash_password(request.password)
    db_user = User(
        username=request.username,
        email=request.email,
        password_hash=hashed_password,
        created_at=get_beijing_time()
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return {
        "success": True,
        "message": "注册成功",
        "userId": db_user.id,
        "username": db_user.username,
        "email": db_user.email
    }

# POST /api/v1/auth/login - 登录
@auth_router.post("/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    # 查询用户
    db_user = db.query(User).filter(User.username == request.username).first()
    if not db_user or not verify_password(request.password, db_user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    
    # 生成JWT token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.username, "user_id": db_user.id},
        expires_delta=access_token_expires
    )
    
    return {
        "success": True,
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": db_user.id,
        "username": db_user.username
    }

# GET /api/v1/auth/user/info - 获取用户信息
@users_router.get("/me")
async def get_user_info(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    token = credentials.credentials
    
    # 验证token
    payload = verify_token(token)
    username = payload.get("sub")
    
    # 查询用户
    db_user = db.query(User).filter(User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    return {
        "success": True,
        "user": {
            "id": db_user.id,
            "username": db_user.username,
            "email": db_user.email,
            "created_at": db_user.created_at.isoformat()
        }
    }

# GET /api/v1/auth/logout - 退出登录
@auth_router.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    return {
        "success": True,
        "message": "logout ok"
    }

# POST /api/v1/auth/refresh - 刷新token
@auth_router.post("/refresh")
async def refresh_token(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """刷新JWT token"""
    token = credentials.credentials
    
    # 验证token
    payload = verify_token(token)
    username = payload.get("sub")
    user_id = payload.get("user_id")
    
    # 查询用户确认存在
    db_user = db.query(User).filter(User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 生成新的JWT token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    new_access_token = create_access_token(
        data={"sub": db_user.username, "user_id": db_user.id},
        expires_delta=access_token_expires
    )
    
    return {
        "success": True,
        "access_token": new_access_token,
        "token_type": "bearer"
    }

# PUT /api/v1/users/me - 更新个人信息
@users_router.put("/me")
async def update_user_info(request: UpdateUserRequest, credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """更新用户信息"""
    token = credentials.credentials
    
    # 验证token
    payload = verify_token(token)
    username = payload.get("sub")
    
    # 查询用户
    db_user = db.query(User).filter(User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 更新用户信息
    if request.username:
        # 检查新用户名是否已存在
        existing_user = db.query(User).filter(User.username == request.username, User.id != db_user.id).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="用户名已存在")
        db_user.username = request.username
    
    if request.password:
        db_user.password_hash = hash_password(request.password)
    
    if request.email:
        db_user.email = request.email
    
    db.commit()
    db.refresh(db_user)
    
    return {
        "success": True,
        "message": "更新成功",
        "user": {
            "id": db_user.id,
            "username": db_user.username,
            "email": db_user.email,
            "created_at": db_user.created_at.isoformat()
        }
    }

# GET /api - 测试接口
@app.get("/api")
async def read_root():
    return "Hello, World!"


# 图片路由 

# POST /api/v1/images/upload - 上传图片
@images_router.post("/upload")
async def upload_image(
    image: UploadFile = File(...), 
    user_id: int = 1, 
    #background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 允许的文件扩展名
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'}
    # 文件大小限制：10MB
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB   
    # 验证文件扩展名
    ext = os.path.splitext(image.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {
            "success": False, 
            "message": f"不支持的文件类型。允许的类型: {', '.join(ALLOWED_EXTENSIONS)}"
        }
    
    # 读取文件数据并验证大小
    try:
        file_data = await image.read()
        file_size = len(file_data)
        
        # 验证文件大小
        if file_size > MAX_FILE_SIZE:
            return {
                "success": False,
                "message": f"文件过大。最大允许大小: 10MB, 当前大小: {file_size / 1024 / 1024:.2f}MB"
            }
        
        if file_size == 0:
            return {
                "success": False,
                "message": "文件为空"
            }
    except Exception as e:
        return {"success": False, "message": f"读取文件失败: {str(e)}"}
    # 生成唯一文件名
    original_name = os.path.splitext(image.filename or "upload")[0]
    unique_filename = f"{original_name}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    # 保存文件到磁盘
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(file_data)
    except Exception as e:
        return {"success": False, "message": f"保存文件失败: {str(e)}"}
    
    # 保存图片信息到数据库
    try:
        db_image = Image(
            user_id=user_id,
            filename=unique_filename,
            path=file_path,
            upload_time=get_beijing_time()
        )
        db.add(db_image)
        db.commit()
        db.refresh(db_image)

        # 异步执行OCR和后续分析流程
        # 后续改为消息队列
        # temp: 禁用
        #if background_tasks is not None:
            #background_tasks.add_task(run_auto_image_pipeline, db_image.id)
        
        return {
            "success": True,
            "imageId": db_image.id,
            "filename": db_image.filename,
            "originalName": image.filename,
            "fileSize": file_size,
            "pipeline_started": True
        }
    except Exception as e:
        db.rollback()
        # 删除已保存的文件
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass
        return {"success": False, "message": f"保存到数据库失败: {str(e)}"}

# GET /api/v1/images/{image_id} - 获取图片
@images_router.get("/{image_id}")
async def get_pic(
    image_id: int, 
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 从数据库查询图片信息
    db_image = db.query(Image).filter(Image.id == image_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")
    if not os.path.exists(str(db_image.path)):
        raise HTTPException(status_code=404, detail="image file not found")
    return FileResponse(str(db_image.path))

# GET /api/v1/images/{image_id} - 获取图片缩略图
@images_router.get("/{image_id}/thumbnail")
async def get_thumbnail(
    image_id: int, 
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # temp: 返回原图
    db_image = db.query(Image).filter(Image.id == image_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")
    if not os.path.exists(str(db_image.path)):
        raise HTTPException(status_code=404, detail="image file not found")

    thumbnail_path = build_thumbnail_path(db_image.filename)
    ensure_thumbnail_exists(str(db_image.path), thumbnail_path)
    return FileResponse(thumbnail_path, media_type="image/jpeg")


# DELETE /api/v1/images/{image_id} - 删除图片及其相关分析结果
@images_router.delete("/{image_id}")
async def delete_image(
    image_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """删除图片，并清理其 OCR/结构化/关系图结果及缩略图文件。"""
    token = credentials.credentials
    payload = verify_token(token)
    user_id = payload.get("user_id")

    db_image = db.query(Image).filter(Image.id == image_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")

    if db_image.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权删除该图片")

    original_path = str(db_image.path)
    thumbnail_path = build_thumbnail_path(db_image.filename)

    structured_ids_query = (
        db.query(StructuredResult.id)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .filter(OcrResult.image_id == image_id)
    )

    deleted_ocr_count = db.query(OcrResult).filter(OcrResult.image_id == image_id).count()
    deleted_structured_count = (
        db.query(StructuredResult)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .filter(OcrResult.image_id == image_id)
        .count()
    )
    deleted_relation_count = db.query(RelationGraph).filter(RelationGraph.structured_result_id.in_(structured_ids_query)).count()
    deleted_association_count = db.query(MultiTaskStructuredResult).filter(MultiTaskStructuredResult.structured_result_id.in_(structured_ids_query)).count()

    try:
        # 对旧库结构做兼容：显式删除下游记录，避免因历史未开启外键级联导致残留。
        db.query(MultiTaskStructuredResult).filter(MultiTaskStructuredResult.structured_result_id.in_(structured_ids_query)).delete(synchronize_session=False)
        db.query(RelationGraph).filter(RelationGraph.structured_result_id.in_(structured_ids_query)).delete(synchronize_session=False)
        db.query(StructuredResult).filter(StructuredResult.id.in_(structured_ids_query)).delete(synchronize_session=False)
        db.query(OcrResult).filter(OcrResult.image_id == image_id).delete(synchronize_session=False)
        db.delete(db_image)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除图片失败: {str(e)}")

    removed_files: List[str] = []
    for path in (original_path, thumbnail_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                removed_files.append(path)
        except OSError:
            # 文件删除失败不影响数据库事务，避免出现“已删除数据库但接口报错”的不一致体验。
            pass

    return {
        "success": True,
        "message": "图片及关联分析结果已删除",
        "deleted": {
            "image_id": image_id,
            "ocr_results": deleted_ocr_count,
            "structured_results": deleted_structured_count,
            "relation_graphs": deleted_relation_count,
            "multi_task_associations": deleted_association_count,
            "removed_files": removed_files
        }
    }

# GET /api/v1/images/{image_id}/info - 获取图片基本信息
@images_router.get("/{image_id}/info")
async def get_image_info(
    image_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定图片的基本信息"""
    # 验证token
    token = credentials.credentials
    verify_token(token)

    # 从数据库查询图片信息
    db_image = db.query(Image).filter(Image.id == image_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")

    return {
        "success": True,
        "data": {
            "id": db_image.id,
            "filename": db_image.filename,
            "upload_time": db_image.upload_time.isoformat(),
            "title": "title_test"
        }
    }

# GET /api/v1/users/images - 获取当前用户的图片列表
@users_router.get("/images")
async def get_user_images(
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取当前用户的图片列表"""
    # 验证token
    token = credentials.credentials
    payload = verify_token(token)
    user_id = payload.get("user_id")
    
    # 验证用户存在
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 查询用户的图片，分页返回id
    images = db.query(Image.id).filter(Image.user_id == user_id).offset(skip).limit(limit).all()
    
    # 获取总数
    total = db.query(Image).filter(Image.user_id == user_id).count()
    
    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [image[0] for image in images]
        }
    }


# OCR路由

# POST /api/v1/images/{image_id}/ocr - 对图片执行OCR
@images_router.post("/{image_id}/ocr")
async def perform_image_ocr(
    image_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """对指定的图片执行OCR"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证图片存在
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="图片不存在")
    
    # 执行OCR (Celery Async)
    task_ocr_image.delay(image_id)
    
    return {
        "success": True,
        "message": f"图片 {image_id} 的OCR任务已提交到队列"
    }

# GET /api/v1/ocr-results/{ocr_id} - 获取特定OCR结果
@ocr_router.get("/{ocr_id}")
async def get_ocr_result(
    ocr_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定id的OCR结果"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 查询OCR结果
    ocr_result = db.query(OcrResult).filter(OcrResult.id == ocr_id).first()
    
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OCR结果不存在")
    
    return {
        "success": True,
        "data": {
            "id": ocr_result.id,
            "image_id": ocr_result.image_id,
            "raw_text": ocr_result.raw_text,
            "status": ocr_result.status.value,
            "created_at": ocr_result.created_at.isoformat()
        }
    }

# GET /api/v1/images/{image_id}/ocr-results - 获取图片的OCR结果列表
@images_router.get("/{image_id}/ocr-results")
async def get_image_ocr_results(
    image_id: int,
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取图片的OCR结果列表"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证图片存在
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="图片不存在")
    
    # 查询OCR结果，分页返回id
    ocr_results = db.query(OcrResult.id).filter(OcrResult.image_id == image_id).offset(skip).limit(limit).all()
    
    # 获取总数
    total = db.query(OcrResult).filter(OcrResult.image_id == image_id).count()
    
    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [result[0] for result in ocr_results]
        }
    }


# 结构化结果路由

# POST /api/v1/structured-results - 对指定OcrResult进行分析
@structured_result_router.post("")
async def create_structured_result(
    request: CreateStructuredResultRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """对指定的OcrResult进行结构化分析"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证OcrResult存在
    ocr_result = db.query(OcrResult).filter(OcrResult.id == request.ocr_result_id).first()
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OcrResult不存在")
    
    # 调用分析函数（Celery Async）
    task_analyze_ocr_result.delay(request.ocr_result_id)
    
    return {
        "success": True,
        "message": f"OCR结果 {request.ocr_result_id} 的结构化分析任务已提交到队列"
    }

# GET /api/v1/structured-results/{structured_result_id} - 获取指定StructuredResult
@structured_result_router.get("/{structured_result_id}")
async def get_structured_result(
    structured_result_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定id的结构化结果"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 查询结构化结果
    structured_result = db.query(StructuredResult).filter(StructuredResult.id == structured_result_id).first()
    
    if not structured_result:
        raise HTTPException(status_code=404, detail="StructuredResult不存在")
    
    # 解析JSON内容
    import json
    try:
        content = json.loads(structured_result.content)
    except:
        content = structured_result.content
    
    return {
        "success": True,
        "data": {
            "id": structured_result.id,
            "ocr_result_id": structured_result.ocr_result_id,
            "content": content,
            "status": structured_result.status.value,
            "created_at": structured_result.created_at.isoformat()
        }
    }

# GET /api/v1/ocr-results/{ocr_result_id}/structured-results - 获取指定OcrResult的StructuredResult列表
@ocr_router.get("/{ocr_result_id}/structured-results")
async def get_ocr_structured_results(
    ocr_result_id: int,
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定OcrResult的结构化结果列表"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证OcrResult存在
    ocr_result = db.query(OcrResult).filter(OcrResult.id == ocr_result_id).first()
    if not ocr_result:
        raise HTTPException(status_code=404, detail="OcrResult不存在")
    
    # 查询结构化结果，分页返回id
    structured_results = db.query(StructuredResult.id).filter(StructuredResult.ocr_result_id == ocr_result_id).offset(skip).limit(limit).all()
    
    # 获取总数
    total = db.query(StructuredResult).filter(StructuredResult.ocr_result_id == ocr_result_id).count()
    
    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [result[0] for result in structured_results]
        }
    }


# 关系图路由

# POST /api/v1/relation-graphs - 对指定StructuredResult进行分析
@relation_graph_router.post("")
async def create_relation_graph(
    request: CreateRelationGraphRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """对指定的StructuredResult进行关系图分析"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证StructuredResult存在
    structured_result = db.query(StructuredResult).filter(StructuredResult.id == request.structured_result_id).first()
    if not structured_result:
        raise HTTPException(status_code=404, detail="StructuredResult不存在")
    
    # 调用分析函数（Celery Async）
    task_analyze_structured_result.delay(request.structured_result_id)
    
    return {
        "success": True,
        "message": f"StructuredResult {request.structured_result_id} 的关系图生成任务已提交到队列"
    }

# GET /api/v1/relation-graphs/{relation_graph_id} - 获取指定RelationGraph
@relation_graph_router.get("/{relation_graph_id}")
async def get_relation_graph(
    relation_graph_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定id的关系图结果"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 查询关系图结果
    relation_graph = db.query(RelationGraph).filter(RelationGraph.id == relation_graph_id).first()
    
    if not relation_graph:
        raise HTTPException(status_code=404, detail="RelationGraph不存在")
    
    # 解析JSON内容
    import json
    try:
        content = json.loads(relation_graph.content)
    except:
        content = relation_graph.content
    
    return {
        "success": True,
        "data": {
            "id": relation_graph.id,
            "structured_result_id": relation_graph.structured_result_id,
            "content": content,
            "status": relation_graph.status.value,
            "created_at": relation_graph.created_at.isoformat()
        }
    }

# GET /api/v1/structured-results/{structured_result_id}/relation-graphs - 获取指定StructuredResult的RelationGraph列表
@structured_result_router.get("/{structured_result_id}/relation-graphs")
async def get_structured_result_relation_graphs(
    structured_result_id: int,
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定StructuredResult的关系图列表"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证StructuredResult存在
    structured_result = db.query(StructuredResult).filter(StructuredResult.id == structured_result_id).first()
    if not structured_result:
        raise HTTPException(status_code=404, detail="StructuredResult不存在")
    
    # 查询关系图结果，分页返回id
    relation_graphs = db.query(RelationGraph.id).filter(RelationGraph.structured_result_id == structured_result_id).offset(skip).limit(limit).all()
    
    # 获取总数
    total = db.query(RelationGraph).filter(RelationGraph.structured_result_id == structured_result_id).count()
    
    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [graph[0] for graph in relation_graphs]
        }
    }


# 多任务路由

# GET /api/v1/users/multi-tasks - 获取指定用户的MultiTask列表
@users_router.get("/multi-tasks")
async def get_user_multi_tasks(
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取当前用户的多任务列表"""
    # 验证token
    token = credentials.credentials
    payload = verify_token(token)
    user_id = payload.get("user_id")
    
    # 验证用户存在
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 查询多任务，分页返回id
    multi_tasks = db.query(MultiTask.id).filter(MultiTask.user_id == user_id).offset(skip).limit(limit).all()
    
    # 获取总数
    total = db.query(MultiTask).filter(MultiTask.user_id == user_id).count()
    
    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [task[0] for task in multi_tasks]
        }
    }

# 跨文档关系图路由

# POST /api/v1/multi-relation-graphs - 对指定MultiTask进行跨文档分析
@multi_relation_graph_router.post("")
async def create_multi_relation_graph(
    request: CreateMultiRelationGraphRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """对指定的MultiTask进行跨文档分析"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 验证MultiTask存在
    multi_task = db.query(MultiTask).filter(MultiTask.id == request.multi_task_id).first()
    if not multi_task:
        raise HTTPException(status_code=404, detail="MultiTask不存在")
    
    # 调用分析函数（Celery Async）
    task_analyze_multi_task.delay(request.multi_task_id)
    
    return {
        "success": True,
        "message": f"MultiTask {request.multi_task_id} 的跨文档分析任务已提交到队列"
    }

# GET /api/v1/multi-relation-graphs/{multi_relation_graph_id} - 获取指定MultiRelationGraph
@multi_relation_graph_router.get("/{multi_relation_graph_id}")
async def get_multi_relation_graph(
    multi_relation_graph_id: int,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """获取指定id的跨文档关系图"""
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    # 查询跨文档关系图
    multi_relation_graph = db.query(MultiRelationGraph).filter(MultiRelationGraph.id == multi_relation_graph_id).first()
    
    if not multi_relation_graph:
        raise HTTPException(status_code=404, detail="MultiRelationGraph不存在")
    
    # 解析JSON内容
    import json
    try:
        content = json.loads(multi_relation_graph.content)
    except:
        content = multi_relation_graph.content
    
    return {
        "success": True,
        "data": {
            "id": multi_relation_graph.id,
            "multi_task_id": multi_relation_graph.multi_task_id,
            "content": content,
            "status": multi_relation_graph.status.value,
            "created_at": multi_relation_graph.created_at.isoformat()
        }
    }


# 智能问答路由

# POST /api/v1/chat/query - 智能问答
@chat_router.post("/query")
async def chat_query(
    request: ChatQueryRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    RAG 对话接口
    """
    # 验证token
    token = credentials.credentials
    verify_token(token)
    
    from app.services.rag_service import rag_pipeline
    
    try:
        result = await rag_pipeline(request.question, db)
        return {
            "success": True,
            "data": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(images_router)
app.include_router(ocr_router)
app.include_router(structured_result_router)
app.include_router(relation_graph_router)
app.include_router(multi_task_router_v2)
# app.include_router(multi_task_router)
app.include_router(multi_relation_graph_router)
app.include_router(chat_router)

if __name__ == "__main__":
    import uvicorn
    # 启动服务
    uvicorn.run(app, host="0.0.0.0", port=3000)