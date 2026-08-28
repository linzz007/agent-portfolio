# 操作日志功能说明

## 概述

操作日志功能用于记录平台内所有用户的操作行为，包括学生修改名称、老师修改证书等操作。前端显示的内容是易懂的描述，如"管理员删除了用户XXX"这样的格式。

## 功能特性

- ✅ 记录所有用户操作
- ✅ 易懂的操作描述（如"管理员删除了用户张三"）
- ✅ 支持按关键词搜索
- ✅ 支持分页显示
- ✅ 显示操作详情
- ✅ 记录操作时间和IP地址
- ✅ 显示操作用户信息

## 数据库结构

操作日志存储在 `operation_logs` 表中，包含以下字段：

- `id`: 日志ID（UUID）
- `tenant_id`: 租户ID
- `account_id`: 操作用户ID
- `action`: 操作描述（如"管理员删除了用户张三"）
- `content`: 操作内容详情（JSON格式）
- `created_at`: 操作时间
- `created_ip`: 操作IP地址
- `updated_at`: 更新时间

## API接口

### 获取操作日志列表

```
GET /console/api/operation-logs
```

**参数：**
- `page`: 页码（默认1）
- `size`: 每页数量（默认20，最大100）
- `keyword`: 关键词搜索

**响应示例：**
```json
{
  "logs": [
    {
      "id": "uuid",
      "action": "管理员修改了学生张三的信息",
      "content": {
        "operation_type": "学生操作",
        "student_info": {
          "student_id": "2023001",
          "student_name": "张三",
          "updated_fields": ["gender", "birth_date"]
        },
        "timestamp": "2024-01-15T10:30:00"
      },
      "created_at": "2024-01-15T10:30:00",
      "created_ip": "192.168.1.100",
      "account": {
        "id": "user-uuid",
        "name": "管理员",
        "email": "<redacted-email>"
      }
    }
  ],
  "total": 100,
  "page": 1,
  "size": 20
}
```

## 前端界面

操作日志界面位于：`/StudentView/information-log`

界面功能：
- 📊 操作日志列表展示
- 🔍 关键词搜索
- 📄 分页浏览
- 👁️ 操作详情查看
- 📱 响应式设计

## 操作日志示例

### 学生相关操作
- "管理员修改了学生张三的信息"
- "管理员删除了学生李四"
- "管理员创建了学生王五"

### 教师相关操作
- "管理员修改了班级CS001的信息"
- "管理员删除了班级CS002"
- "管理员修改了老师赵六的证书"

### 系统操作
- "管理员登录了系统"
- "管理员修改了系统配置"
- "管理员导入了学生数据"

## 如何记录操作日志

### 1. 使用工具函数

```python
from libs.operation_log import record_operation_log, record_student_operation, record_teacher_operation

# 记录一般操作
record_operation_log(
    action="管理员删除了用户张三",
    content={
        "user_id": "user123",
        "user_name": "张三",
        "deleted_at": "2024-01-15T10:30:00"
    }
)

# 记录学生操作（便捷函数）
record_student_operation(
    action="管理员修改了学生李四的姓名",
    student_info={
        "student_id": "2023001",
        "student_name": "李四",
        "old_name": "李四",
        "new_name": "李四四"
    }
)

# 记录教师操作（便捷函数）
record_teacher_operation(
    action="管理员修改了老师王五的证书",
    target_info={
        "teacher_id": "T001",
        "teacher_name": "王五",
        "certificate_type": "教师资格证",
        "status": "已更新"
    }
)
```

### 2. 在控制器中使用

```python
from libs.operation_log import record_student_operation

class StudentUpdateAPI(Resource):
    def post(self):
        # ... 更新学生信息的代码 ...
        
        # 记录操作日志
        record_student_operation(
            action=f"管理员修改了学生{student.student_name}的信息",
            student_info={
                "student_id": student.student_id,
                "student_name": student.student_name,
                "updated_fields": list(data.keys())
            }
        )
        
        return {"message": "更新成功"}, 200
```

## 测试

运行测试脚本验证功能：

```bash
python test_operation_log.py
```

## 注意事项

1. **操作描述格式**: 使用易懂的描述，如"管理员删除了用户XXX"
2. **性能考虑**: 操作日志记录是异步的，不会影响主业务流程
3. **数据安全**: 敏感信息（如密码）不会记录在日志中
4. **存储管理**: 建议定期清理过期的操作日志
5. **权限控制**: 只有管理员可以查看操作日志

## 故障排查

### 常见问题

1. **日志没有记录**
   - 检查数据库连接
   - 确认用户已登录
   - 查看后端日志错误信息

2. **前端显示异常**
   - 检查API接口是否正常
   - 确认网络连接
   - 查看浏览器控制台错误

3. **搜索功能不工作**
   - 确认搜索参数格式正确
   - 检查数据库索引
   - 验证关键词匹配逻辑

### 调试方法

1. 查看后端日志：
```bash
tail -f logs/app.log
```

2. 检查数据库：
```sql
SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 10;
```

3. 测试API接口：
```bash
curl -X GET "http://localhost:5001/console/api/operation-logs?page=1&size=5"
``` 