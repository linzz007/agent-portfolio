from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

from .engine import db
from .types import StringUUID

from sqlalchemy import func

class Student_file(Base):
    __tablename__ = "student_file"
    __table_args__ = (
        db.PrimaryKeyConstraint("student_id", name="student_file_pkey"),
        db.Index("student_file_idx", "student_id"),
    )

    student_id = db.Column(db.String(10), nullable=False)  # 学号
    student_name = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 姓名
    gender = db.Column(db.String(2), nullable=True, server_default=db.text("'default'"))  # 性别
    birth_date = db.Column(db.Date, nullable=True)  # 出生日期
    native_place = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 籍贯
    ethnicity = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 民族
    id_type = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 身份证类型
    id_number = db.Column(db.String(30), nullable=True, server_default=db.text("'default'"))  # 身份证号
    department_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 院系代码
    enrollment_date = db.Column(db.Date, nullable=True)  # 入学日期
    class_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 班级代码
    class_name = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 班级名称
    major_code = db.Column(db.String(6), nullable=True, server_default=db.text("'default'"))  # 专业代码
    major_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 专业名称
    department_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 院系名称
    student_status = db.Column(db.String(2), nullable=True, server_default=db.text("'default'"))  # 学籍状态
    education_level = db.Column(db.String(2), nullable=True, server_default=db.text("'default'"))  # 学历层次
    school_system = db.Column(db.String(10), nullable=True, server_default=db.text("'default'"))  # 学制
    study_type = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 学习形式
    graduation_date = db.Column(db.Date, nullable=True)  # 毕业日期
    campus_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 校区代码
    grade_level = db.Column(db.String(4), nullable=True, server_default=db.text("'default'"))  # 年级
    political_status = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 政治面貌
    resume = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))  # 简历
    phone = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 联系电话
    email = db.Column(db.String(255), nullable=True, server_default=db.text("'default'"))  # 电子邮箱
    job_intention = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))  # 求职意向

class Teacher_file(Base):
    __tablename__ = "teacher_file"
    __table_args__ = (
        db.PrimaryKeyConstraint("teacher_id", name="teacher_file_pkey"),
        db.Index("teacher_file_idx", "teacher_id"),
    )
    
    teacher_id = db.Column(db.String(10), nullable=False)  # 职工号
    teacher_name = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 姓名
    gender = db.Column(db.String(2), nullable=True, server_default=db.text("'default'"))  # 性别
    department_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 单位代码
    department_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 单位名称
    email = db.Column(db.String(255), nullable=True, server_default=db.text("'default'"))  # 电子邮箱
    status_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 人员状态代码
    status_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 人员状态名称
    phone = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 手机号码

class Dean_file(Base):
    __tablename__ = "dean_file"
    __table_args__ = (
        db.PrimaryKeyConstraint("dean_id", name="dean_file_pkey"),
        db.Index("dean_file_idx", "dean_id"),
    )
    
    dean_id = db.Column(db.String(10), nullable=False)  # 院长ID（对应teacher_file.teacher_id）
    dean_name = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 院长姓名
    gender = db.Column(db.String(2), nullable=True, server_default=db.text("'default'"))  # 性别
    department_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 单位代码
    department_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 单位名称
    email = db.Column(db.String(255), nullable=True, server_default=db.text("'default'"))  # 电子邮箱
    status_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 人员状态代码
    status_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 人员状态名称
    phone = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 手机号码

class Principle_file(Base):
    __tablename__ = "principal_file"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="principal_file_pkey"),
        db.Index("principal_file_idx", "id"),
    )
    
    id = db.Column(db.String(10), nullable=False)  # 校长ID（对应teacher_file.teacher_id）
    name = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 校长姓名
    gender = db.Column(db.String(2), nullable=True, server_default=db.text("'default'"))  # 性别
    email = db.Column(db.String(255), nullable=True, server_default=db.text("'default'"))  # 电子邮箱
    status_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 人员状态代码
    status_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 人员状态名称
    phone = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 手机号码
    department_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 单位代码
    department_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 单位名称

class Class_file(Base):
    __tablename__ = "class_file"
    __table_args__ = (
        db.PrimaryKeyConstraint("class_code", name="class_file_pkey"),
        db.Index("class_file_idx", "class_code"),
        db.UniqueConstraint("class_code", name="uix_class_code"),
    )
    class_code = db.Column(db.String(20), nullable=False, unique=True, server_default=db.text("'default'"))  # 班级代码
    class_name = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 班级名称
    teacher_id = db.Column(db.String(10), nullable=False, server_default=db.text("'default'"))  # 班主任ID
    class_number = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 班号
    counselor = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 辅导员ID
    director = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))  # 学工办主任姓名
    department_code = db.Column(db.String(20), nullable=True, server_default=db.text("'default'"))  # 院系代码
    department_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))  # 院系名称


class CampusExperience(Base):
    __tablename__ = "campus_experience"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="campus_experience_pkey"),
        db.Index("campus_experience_idx", "id"),
    )

    id = db.Column(StringUUID, primary_key=True, server_default=db.text("gen_random_uuid()"))
    account_id = db.Column(StringUUID, nullable=False)
    student_organization = db.Column(db.String(100), nullable=True, server_default=db.text("'default'")) #学生组织
    position = db.Column(db.String(50), nullable=True, server_default=db.text("'default'")) #职务
    experience_period = db.Column(db.String(50), nullable=True, server_default=db.text("'default'"))#经历时间
    content = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))#主要职责成果

class AwardCertificate(Base):
    __tablename__ = "award_certificate"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="award_certificate_pkey"),
        db.Index("award_certificate_idx", "id"),
    )

    id = db.Column(StringUUID, primary_key=True, server_default=db.text("gen_random_uuid()"))
    account_id = db.Column(StringUUID, nullable=False)
    award_name = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))#奖项名称
    award_date = db.Column(db.Date, nullable=True)#获奖时间
    awarding_unit = db.Column(db.String(100), nullable=True, server_default=db.text("'default'"))#颁奖单位
    content = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))##主要职责成果
    proof = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))##证明
    
class AllExperience(Base):
    __tablename__ = "all_experience"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="all_experience_pkey"),
        db.Index("all_experience_idx", "id"),
    )

    id = db.Column(StringUUID, primary_key=True, server_default=db.text("gen_random_uuid()"))
    xh = db.Column(db.String(30), nullable=False)
    experience = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))#经历
    proof_url = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))##证明

class StudentSummaryReport(Base):
    __tablename__ = "student_summary_report"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="student_summary_report_pkey"),
        db.Index("student_summary_report_idx", "id"),
    )

    id = db.Column(StringUUID, primary_key=True, server_default=db.text("gen_random_uuid()"))
    xy = db.Column(db.String(30), nullable=False)
    bj = db.Column(db.String(30), nullable=False)
    xh = db.Column(db.String(30), nullable=False)
    content = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))#内容
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp(), comment="创建时间")

class AllSummaryReport(Base):
    __tablename__ = "all_summary_report"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="all_summary_report_pkey"),
        db.Index("all_summary_report_idx", "id"),
    )

    id = db.Column(StringUUID, primary_key=True, server_default=db.text("gen_random_uuid()"))
    flag_level = db.Column(db.Boolean, nullable=False)#是否为校领导
    xy = db.Column(db.String(30), nullable=False)
    bj = db.Column(db.String(30), nullable=False)
    content = db.Column(db.Text, nullable=True, server_default=db.text("'default'"))#内容
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp(), comment="创建时间")
    


