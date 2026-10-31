"""knowhow 统一配置：所有敏感/可变参数从环境变量读取。

加载优先级：
1. 环境变量（生产推荐：systemd/launchd/pm2 注入）
2. .env 文件（本地开发：仓库根目录 .env，已被 gitignore，勿提交）
3. 内置开发默认值（仅本地可用，生产必须显式设置）

生产必设项（见 .env.example / docs/生产部署手册.md）：
- RAG_DSN       数据库连接串（务必改掉开发密码 rag_dev_2026）
- KNOWHOW_SECRET 随机密钥（会话签名/随机种子；不设则启动时自生成并在日志警告）
- SEED_PASSWORDS 种子账号密码覆盖（生产默认随机生成，启动时打印一次）
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

# 支持 dotenv：尝试加载仓库根 .env（缺 python-dotenv 则忽略）
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
except ImportError:
    pass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ---------------- 数据库 ----------------
# 开发默认：docker compose 里的 rag 用户（5433 是宿主机映射端口，5432 被系统 PG 占用）
RAG_DSN: str = _get("RAG_DSN", "postgresql://rag:rag_dev_2026@localhost:5433/rag")

# ---------------- 密钥 ----------------
# 会话随机种子/签名密钥：未显式设置时生成随机值（进程重启后会话失效，可接受）
SECRET_KEY: str = _get("KNOWHOW_SECRET") or secrets.token_urlsafe(32)
if not _get("KNOWHOW_SECRET"):
    print("[config] 警告: 未设置 KNOWHOW_SECRET，已生成随机临时密钥（重启后所有会话失效）")

# ---------------- 会话 ----------------
SESSION_TTL_HOURS: int = int(_get("SESSION_TTL_HOURS", "24"))          # 单会话有效时长
SESSION_MAX_HOURS: int = int(_get("SESSION_MAX_HOURS", "72"))           # 最长登录周期（超时强制重登）
LOGIN_MAX_ATTEMPTS: int = int(_get("LOGIN_MAX_ATTEMPTS", "5"))          # 连续失败次数
LOGIN_LOCK_MINUTES: int = int(_get("LOGIN_LOCK_MINUTES", "10"))         # 超限锁定分钟

# ---------------- 生产模式与种子账号 ----------------
# PROD=1 时：不打印明文默认密码提示、强制首次登录改密
PROD: bool = _get("KNOWHOW_PROD", "0") == "1"

# 种子账号覆盖格式：u_admin=强密码,u_reviewer=xxx（生产部署时由运维注入）
SEED_PASSWORDS: dict[str, str] = {}
_seed_raw = _get("SEED_PASSWORDS")
if _seed_raw:
    for item in _seed_raw.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            SEED_PASSWORDS[k.strip()] = v.strip()


# ---------------- 服务 ----------------
APP_HOST: str = _get("APP_HOST", "0.0.0.0")
APP_PORT: int = int(_get("APP_PORT", "8000"))
MAX_UPLOAD_MB: int = int(_get("MAX_UPLOAD_MB", "50"))                   # 上传文件大小上限

# ---------------- 模型 ----------------
EMBEDDING_MODEL: str = _get("EMBEDDING_MODEL", "bge-m3")               # bge-m3 | qwen3-embedding-0.6b
MODEL_CACHE_DIR: Optional[str] = _get("MODEL_CACHE_DIR") or None       # 缺省走 HF 缓存


def dsn_for_container() -> str:
    """容器内数据库地址（docker compose 网络内主机名 db）。"""
    return _get("RAG_DSN_CONTAINER", "postgresql://rag:rag_dev_2026@db:5432/rag")
