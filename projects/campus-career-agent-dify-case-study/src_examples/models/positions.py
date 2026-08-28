from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

from .engine import db
from .types import StringUUID

from sqlalchemy import func

from models.model import StringUUID

class Position(Base):
    __tablename__ = "position"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="position_pkey"),
        db.Index("position_dataset_idx", "dataset_id"),
    )

    id = db.Column(StringUUID, server_default=db.text("uuid_generate_v4()"))
    dataset_id = db.Column(StringUUID, nullable=False)
    title = db.Column(db.String(255), nullable=False)  # 岗位名称
    description = db.Column(db.Text, nullable=False)  # 职责描述
    requirements = db.Column(db.Text, nullable=True)  # 岗位要求
    
    # 元数据字段
    created_by = db.Column(StringUUID, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp())
    
    