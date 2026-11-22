import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def create_database():
    try:
        # 连接到默认的 'postgres' 数据库
        con = psycopg2.connect(dbname='postgres', user='postgres', host='localhost', password='zhouxu')
        con.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = con.cursor()
        
        # 检查目标数据库是否已存在
        cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'smart_edu'")
        exists = cur.fetchone()
        
        if not exists:
            cur.execute('CREATE DATABASE smart_edu')
            print("已创建数据库 'smart_edu'。")
        else:
            print("数据库 'smart_edu' 已存在。")
            
        cur.close()
        con.close()
    except Exception as e:
        print(f"创建数据库出错：{e}")

if __name__ == "__main__":
    create_database()
