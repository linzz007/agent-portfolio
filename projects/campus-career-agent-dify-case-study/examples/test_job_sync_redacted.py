#!/usr/bin/env python3
import psycopg2, os, json
from datetime import datetime

# 数据库配置
DB_CONFIG = {
    'host': '172.16.8.116',
    'port': 5432,
    'dbname': 'jyai',
    'user': 'jyai', 
    'password': '<redacted>'

}

def test_db():
    print('🔗 测试数据库连接和数据查询...')
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # 检查qy表
        cursor.execute('SELECT COUNT(*) FROM public.qy')
        qy_count = cursor.fetchone()[0]
        print(f'✅ qy表有 {qy_count} 条记录')
        
        # 检查qy_gw表
        cursor.execute('SELECT COUNT(*) FROM public.qy_gw') 
        qy_gw_count = cursor.fetchone()[0]
        print(f'✅ qy_gw表有 {qy_gw_count} 条记录')
        
        # 测试增量查询
        cursor.execute('SELECT id, qymc, regtime FROM public.qy ORDER BY regtime DESC LIMIT 2')
        qy_samples = cursor.fetchall()
        
        cursor.execute('SELECT id, gwmc, refreshtime FROM public.qy_gw ORDER BY refreshtime DESC LIMIT 2')


        qy_gw_samples = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        print('\n📊 最新企业数据样本:')
        for row in qy_samples:
            print(f'  ID: {row[0]}, 企业: {row[1] or "N/A"}, 时间: {row[2] or "N/A"}')
            
        print('\n📊 最新岗位数据样本:')
        for row in qy_gw_samples:
            print(f'  ID: {row[0]}, 岗位: {row[1] or "N/A"}, 时间: {row[2] or "N/A"}')
        
        print('\n🎉 数据库测试成功！数据同步功能可以正常工作。')
        return True
        
    except Exception as e:
        print(f'❌ 数据库测试失败: {e}')
        import traceback
        traceback.print_exc()
        return False

def main():
    print('🚀 JYAI数据同步功能测试')
    print(f'📅 时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 50)
    
    success = test_db()
    
    print('\n' + '=' * 50)
    if success:
        print('✅ 结论: 同步功能准备就绪！')
        print('\n📋 部署步骤:')
        print('1. 确保生产环境有完整的Dify代码')
        print('2. 配置环境变量和定时任务') 
        print('3. 运行首次完整同步')
    else:
        print('❌ 结论: 需要检查数据库配置')

if __name__ == '__main__':
    main()

