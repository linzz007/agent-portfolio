#!/usr/bin/env python3
"""
测试操作日志功能的脚本
"""

import requests
import json
from datetime import datetime

# 配置
BASE_URL = "http://localhost:5001"  # 根据实际部署情况调整
API_BASE = f"{BASE_URL}/console/api"

def test_operation_logs():
    """测试操作日志API"""
    print("=== 测试操作日志功能 ===")
    
    # 1. 获取操作日志列表
    print("\n1. 获取操作日志列表...")
    try:
        response = requests.get(f"{API_BASE}/operation-logs", params={
            "page": 1,
            "size": 10
        })
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 成功获取操作日志")
            print(f"   总数: {data.get('total', 0)}")
            print(f"   当前页: {data.get('page', 1)}")
            print(f"   日志数量: {len(data.get('logs', []))}")
            
            # 显示前几条日志
            logs = data.get('logs', [])
            for i, log in enumerate(logs[:3]):
                print(f"   日志 {i+1}:")
                print(f"     - 时间: {log.get('created_at', 'N/A')}")
                print(f"     - 操作: {log.get('action', 'N/A')}")
                print(f"     - 用户: {log.get('account', {}).get('name', 'N/A')}")
                print(f"     - IP: {log.get('created_ip', 'N/A')}")
        else:
            print(f"❌ 获取操作日志失败: {response.status_code}")
            print(f"   响应: {response.text}")
            
    except Exception as e:
        print(f"❌ 请求失败: {e}")
    
    # 2. 测试搜索功能
    print("\n2. 测试搜索功能...")
    try:
        response = requests.get(f"{API_BASE}/operation-logs", params={
            "page": 1,
            "size": 5,
            "keyword": "管理员"
        })
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 搜索成功")
            print(f"   搜索结果数量: {len(data.get('logs', []))}")
        else:
            print(f"❌ 搜索失败: {response.status_code}")
            
    except Exception as e:
        print(f"❌ 搜索请求失败: {e}")

def test_student_operations():
    """测试学生相关操作（需要先登录）"""
    print("\n=== 测试学生操作 ===")
    print("注意: 这些操作需要先登录系统")
    
    # 这里可以添加测试学生信息更新的代码
    # 由于需要登录认证，这里只是示例
    print("学生操作测试需要登录认证，请在实际环境中测试")

if __name__ == "__main__":
    test_operation_logs()
    test_student_operations()
    
    print("\n=== 测试完成 ===")
    print("如果看到操作日志数据，说明功能正常工作")
    print("如果没有数据，可能需要先进行一些操作来生成日志")
    print("\n示例操作日志内容:")
    print("- 管理员修改了学生张三的信息")
    print("- 管理员修改了班级CS001的信息") 
    print("- 管理员删除了班级CS002") 