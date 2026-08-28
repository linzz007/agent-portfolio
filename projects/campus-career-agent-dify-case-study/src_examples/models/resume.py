from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base
from .engine import db
from .types import StringUUID

class Resume(Base):
    __tablename__ = "resume"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="resume_pkey"),
        db.Index("resume_idx", "id"),
    )

    id = db.Column(StringUUID, primary_key=True, server_default=db.text("uuid_generate_v4()"))
    name = db.Column(db.String(50), nullable=False)
    target_position = db.Column(db.String(100))
    education = db.Column(db.String(20))
    work_experience = db.Column(db.Text)
    skills = db.Column(db.JSON)
    status = db.Column(db.String(20))  # unreviewed/approved/rejected
    applicant_id = db.Column(StringUUID, db.ForeignKey('accounts.id'))
    created_at = db.Column(db.DateTime, server_default=func.now())