from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, LargeBinary
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    balance: Mapped[int] = mapped_column(Integer, default=0)

class Admin(Base):
    __tablename__ = "admins"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    permissions: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(20), default="FOLDER")
    price: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Stock(Base):
    __tablename__ = "stock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    raw_text: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    __table_args__ = (UniqueConstraint("product_id", "digest"),)

class Deposit(Base):
    __tablename__ = "deposits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(Integer)
    txid: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="PENDING")

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    product_name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    delivery: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockBatch(Base):
    __tablename__ = "stock_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    admin_id: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(255), default="")
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    added_items: Mapped[int] = mapped_column(Integer, default=0)
    original_file: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockPayload(Base):
    __tablename__ = "stock_payloads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("stock_batches.id"))
    mode: Mapped[str] = mapped_column(String(20))
    header_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    record_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    filename: Mapped[str] = mapped_column(String(255), default="")


class ProductStockSchema(Base):
    __tablename__ = "product_stock_schemas"
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    format_text: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
