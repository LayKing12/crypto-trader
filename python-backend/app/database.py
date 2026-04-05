from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from app.config import get_settings

settings = get_settings()

if not settings.database_url:
    raise RuntimeError("DATABASE_URL est vide.")

engine = create_async_engine(
    "postgresql+asyncpg://",
    connect_args={
        "host": "aws-1-eu-north-1.pooler.supabase.com",
        "port": 6543,
        "user": "postgres.lgdpbuyottatmfzfupjt",
        "password": "5xtfLzl0VgbqFwdD",
        "database": "postgres",
        "statement_cache_size": 0,
    },
    echo=settings.app_env == "development",
    poolclass=NullPool,
)

print("[DB] Connexion vers Supabase aws-1-eu-north-1...")

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)