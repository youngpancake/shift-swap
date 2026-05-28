from __future__ import annotations
import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

# Use DATABASE_URL from environment (Render PostgreSQL) or fall back to local SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./shift_swap.db")

# Render sets DATABASE_URL as postgres://... but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite needs check_same_thread; PostgreSQL does not
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class ResidentRow(Base):
    __tablename__ = "residents"

    id    = Column(Integer, primary_key=True, index=True)
    name  = Column(String, unique=True, nullable=False)
    level = Column(String, nullable=False, default="Unknown")  # Sr | Jr | Unknown


class ShiftAssignmentRow(Base):
    __tablename__ = "shift_assignments"

    id           = Column(Integer, primary_key=True, index=True)
    resident_id  = Column(Integer, nullable=False)
    work_date    = Column(Date, nullable=False)
    shift_name   = Column(String, nullable=False)
    shift_type   = Column(String, nullable=False, default="Unknown")
    seniority    = Column(String, nullable=False, default="Unknown")
    shift_area   = Column(String, nullable=False, default="")
    is_swappable = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("resident_id", "work_date", name="uq_resident_date"),
        Index("ix_work_date", "work_date"),
    )


class MarketplaceRequestRow(Base):
    __tablename__ = "marketplace_requests"

    id            = Column(Integer, primary_key=True, index=True)
    resident_id   = Column(Integer, nullable=False)
    requested_date = Column(Date, nullable=False)

    __table_args__ = (
        UniqueConstraint("resident_id", "requested_date", name="uq_mp_resident_date"),
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
