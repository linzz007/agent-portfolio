from flask_restful import Resource
from flask import request
from controllers.console import api
from controllers.console.wraps import setup_required
from models.account import Account
import flask_login
import json
import logging

# 配置日志
logger = logging.getLogger(__name__)

class UserRoleApi(Resource):
    """获取当前用户角色信息"""
    
    @setup_required
    def get(self):
        logger.info("=== 开始获取用户角色信息 ===")
        
        account = flask_login.current_user
        if isinstance(account, flask_login.AnonymousUserMixin):
            logger.warning("用户未登录，返回非学生身份")
            return {"isStudent": False}
        
        logger.info(f"当前登录用户: ID={account.id}, 姓名={account.name}, 邮箱={account.email}")
        
        # 检查用户是否为学生
        isStudent = False
        
        # 方法1: 从cookie中获取departments信息
        departments_cookie = request.cookies.get('departments')
        logger.info(f"从cookie获取departments: {departments_cookie}")
        
        if departments_cookie:
            try:
                departments = json.loads(departments_cookie)
                logger.info(f"解析departments JSON: {departments}")
                
                if isinstance(departments, list) and '学生' in departments:
                    isStudent = True
                    logger.info("通过departments列表判断: 用户是学生")
                elif isinstance(departments, str) and '学生' in departments:
                    isStudent = True
                    logger.info("通过departments字符串判断: 用户是学生")
                else:
                    logger.info(f"departments中不包含'学生': {departments}")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"解析departments JSON失败: {e}")
                # 如果解析失败，继续尝试其他方法
                pass
        
        # 方法2: 通过邮箱判断学生身份
        if not isStudent and account.email and ('student' in account.email.lower() or 'stu' in account.email.lower()):
            isStudent = True
            logger.info(f"通过邮箱判断: 用户是学生 (邮箱: {account.email})")
        
        # 方法3: 查询学生表判断身份（如果存在学生表）
        if not isStudent:
            try:
                from models.student_teacher import Student_file
                from extensions.ext_database import db
                # 通过邮箱或用户名查询学生表
                student = db.session.query(Student_file).filter(
                    Student_file.student_name == account.name
                ).first()
                if student:
                    isStudent = True
                    logger.info(f"通过学生表查询判断: 用户是学生 (学号: {student.student_id})")
                else:
                    logger.info(f"学生表中未找到用户: {account.name}")
            except Exception as e:
                logger.warning(f"查询学生表失败: {e}")
                # 如果查询失败，保持默认角色
                pass
        
        logger.info(f"最终判断结果: isStudent = {isStudent}")
        logger.info("注意: 此API已不再用于跳转判断，所有用户都会跳转到聊天页面")
        logger.info("=== 用户角色信息获取完成 ===")
        
        return {"isStudent": isStudent}

api.add_resource(UserRoleApi, "/user-role")
