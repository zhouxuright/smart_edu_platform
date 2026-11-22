import asyncio
import sys
import os

# 为导入应用模块，将父目录加入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edu_app.core.database import AsyncSessionLocal, engine, Base
from edu_app.models.learning import KnowledgeNode, StudentProfile, LearningRecord
from edu_app.models.user import User
from edu_app.services import auth
from sqlalchemy import select
from datetime import datetime, timedelta, UTC

# 高中数学真实数据（简体中文）
KNOWLEDGE_DATA = [
    {
        "subject": "数学",
        "name": "集合",
        "description": "集合论的基础概念，包括集合的定义、表示方法（列举法、描述法）、集合间的关系（子集、真子集、相等）以及集合的运算（交集、并集、补集）。",
        "prerequisites": [],
        "related_nodes": ["函数概念", "逻辑"]
    },
    {
        "subject": "数学",
        "name": "函数概念",
        "description": "函数的定义、定义域、值域，函数的表示法（解析法、列表法、图象法），以及分段函数。",
        "prerequisites": ["集合"],
        "related_nodes": ["函数的性质", "指数函数", "对数函数"]
    },
    {
        "subject": "数学",
        "name": "函数的性质",
        "description": "函数的单调性（增函数、减函数）、奇偶性（奇函数、偶函数）、周期性以及最值问题。",
        "prerequisites": ["函数概念"],
        "related_nodes": ["函数概念", "导数"]
    },
    {
        "subject": "数学",
        "name": "指数函数",
        "description": "指数函数的定义、图象与性质，指数运算规则。",
        "prerequisites": ["函数概念", "函数的性质"],
        "related_nodes": ["对数函数"]
    },
    {
        "subject": "数学",
        "name": "对数函数",
        "description": "对数的概念、运算性质（换底公式），对数函数的定义、图象与性质。",
        "prerequisites": ["指数函数"],
        "related_nodes": ["指数函数"]
    },
    {
        "subject": "数学",
        "name": "幂函数",
        "description": "幂函数的定义、常见幂函数的图象与性质。",
        "prerequisites": ["函数概念"],
        "related_nodes": ["函数的性质"]
    },
    {
        "subject": "数学",
        "name": "立体几何",
        "description": "空间几何体（柱、锥、台、球）的结构特征、三视图和直观图，空间点、直线、平面之间的位置关系。",
        "prerequisites": ["平面几何"],
        "related_nodes": ["空间向量"]
    },
    {
        "subject": "数学",
        "name": "平面解析几何",
        "description": "直线的方程（点斜式、两点式、一般式）、圆的方程，直线与圆的位置关系。",
        "prerequisites": ["函数概念", "平面几何"],
        "related_nodes": ["圆锥曲线"]
    },
    {
        "subject": "数学",
        "name": "算法初步",
        "description": "算法的概念、程序框图（顺序结构、条件结构、循环结构）、基本算法语句。",
        "prerequisites": [],
        "related_nodes": ["计算机科学"]
    },
    {
        "subject": "数学",
        "name": "三角函数",
        "description": "任意角和弧度制，任意角的三角函数，三角函数的诱导公式，三角函数的图象与性质。",
        "prerequisites": ["函数概念", "平面几何"],
        "related_nodes": ["解三角形", "平面向量"]
    },
    {
        "subject": "数学",
        "name": "平面向量",
        "description": "平面向量的实际背景及基本概念，向量的线性运算，平面向量的数量积。",
        "prerequisites": ["平面几何", "三角函数"],
        "related_nodes": ["立体几何", "解三角形"]
    },
    {
        "subject": "数学",
        "name": "数列",
        "description": "数列的概念与简单表示法，等差数列及其前n项和，等比数列及其前n项和。",
        "prerequisites": ["函数概念"],
        "related_nodes": ["数学归纳法"]
    },
    {
        "subject": "数学",
        "name": "不等式",
        "description": "不等式的性质，一元二次不等式，二元一次不等式组与线性规划，基本不等式。",
        "prerequisites": ["函数概念"],
        "related_nodes": ["函数的最值"]
    },
    {
        "subject": "数学",
        "name": "概率",
        "description": "随机事件的概率，古典概型，几何概型。",
        "prerequisites": ["集合"],
        "related_nodes": ["统计"]
    },
    {
        "subject": "数学",
        "name": "统计",
        "description": "随机抽样，用样本估计总体，变量间的相关关系。",
        "prerequisites": ["概率"],
        "related_nodes": ["概率"]
    },
    {
        "subject": "数学",
        "name": "导数及其应用",
        "description": "导数的概念与运算，导数在研究函数中的应用（单调性、极值、最值），定积分的概念。",
        "prerequisites": ["函数概念", "函数的性质"],
        "related_nodes": ["微积分"]
    }
]

# 高中物理真实数据（简体中文）— 数据来源：2025 课程标准
PHYSICS_DATA = [
    {
        "subject": "物理",
        "name": "运动学",
        "description": "描述物体运动的物理量（位移、速度、加速度），匀变速直线运动的规律，自由落体运动。",
        "prerequisites": [],
        "related_nodes": ["牛顿运动定律"]
    },
    {
        "subject": "物理",
        "name": "牛顿运动定律",
        "description": "牛顿第一定律（惯性），牛顿第二定律（F=ma），牛顿第三定律（作用力与反作用力），以及单位制。",
        "prerequisites": ["运动学"],
        "related_nodes": ["万有引力与航天", "机械能"]
    },
    {
        "subject": "物理",
        "name": "万有引力与航天",
        "description": "开普勒行星运动定律，万有引力定律，宇宙速度，人造卫星。",
        "prerequisites": ["牛顿运动定律", "曲线运动"],
        "related_nodes": ["经典力学"]
    },
    {
        "subject": "物理",
        "name": "机械能",
        "description": "功与功率，动能定理，重力势能，机械能守恒定律，能量守恒定律。",
        "prerequisites": ["牛顿运动定律"],
        "related_nodes": ["动量"]
    },
    {
        "subject": "物理",
        "name": "静电场",
        "description": "电荷守恒定律，库仑定律，电场强度，电势能与电势，电容与电容器。",
        "prerequisites": ["机械能"],
        "related_nodes": ["恒定电流"]
    },
    {
        "subject": "物理",
        "name": "恒定电流",
        "description": "欧姆定律，电阻定律，焦耳定律，闭合电路欧姆定律，多用电表的使用。",
        "prerequisites": ["静电场"],
        "related_nodes": ["磁场"]
    },
    {
        "subject": "物理",
        "name": "磁场",
        "description": "磁感应强度，磁感线，安培力，洛伦兹力，带电粒子在磁场中的运动。",
        "prerequisites": ["恒定电流"],
        "related_nodes": ["电磁感应"]
    },
    {
        "subject": "物理",
        "name": "电磁感应",
        "description": "磁通量，法拉第电磁感应定律，楞次定律，自感与互感。",
        "prerequisites": ["磁场"],
        "related_nodes": ["交变电流"]
    },
    {
        "subject": "物理",
        "name": "交变电流",
        "description": "交变电流的产生与描述，变压器，电能的输送。",
        "prerequisites": ["电磁感应"],
        "related_nodes": ["传感器"]
    },
    {
        "subject": "物理",
        "name": "原子物理",
        "description": "光电效应，原子结构模型（玻尔模型），原子核的组成，放射性衰变，核能。",
        "prerequisites": ["能量守恒"],
        "related_nodes": ["量子力学初步"]
    }
]

async def seed_data():
    print("若不存在则创建数据库表...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # 1. 知识点入库
        print("Checking existing knowledge data...")
        result = await db.execute(select(KnowledgeNode))
        existing = result.scalars().all()
        existing_names = {node.name for node in existing}
        
        all_data = KNOWLEDGE_DATA + PHYSICS_DATA
        new_nodes = []
        for item in all_data:
            if item["name"] not in existing_names:
                new_nodes.append(KnowledgeNode(**item))
        
        if new_nodes:
            print(f"Seeding {len(new_nodes)} new knowledge nodes...")
            for node in new_nodes:
                db.add(node)
            await db.commit()
            print("Knowledge seeding complete!")
        else:
            print("All knowledge nodes already exist.")

        # 2. 创建演示用户
        print("Checking for demo user...")
        demo_email = "student@example.com"
        result = await db.execute(select(User).where(User.email == demo_email))
        user = result.scalars().first()
        
        if not user:
            print("Creating demo user...")
            hashed_pw = auth.get_password_hash("password123")
            user = User(
                email=demo_email,
                hashed_password=hashed_pw,
                full_name="张三",
                role="student"
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            print(f"Created user: {user.email} (ID: {user.id})")
            
            # 创建学生档案
            profile = StudentProfile(
                user_id=user.id,
                grade="高一",
                interests="数学, 物理, 编程",
                learning_style="视觉型"
            )
            db.add(profile)
            
            # 创建学习记录
            print("Seeding learning records...")
            records = [
                LearningRecord(user_id=user.id, subject="数学", topic="集合", activity_type="quiz", score=95.0, timestamp=datetime.now(UTC) - timedelta(days=5)),
                LearningRecord(user_id=user.id, subject="数学", topic="函数概念", activity_type="quiz", score=82.0, timestamp=datetime.now(UTC) - timedelta(days=4)),
                LearningRecord(user_id=user.id, subject="数学", topic="指数函数", activity_type="discussion", score=88.0, timestamp=datetime.now(UTC) - timedelta(days=3)),
                LearningRecord(user_id=user.id, subject="数学", topic="立体几何", activity_type="exam", score=78.0, timestamp=datetime.now(UTC) - timedelta(days=1)),
            ]
            for r in records:
                db.add(r)
            
            await db.commit()
            print("Demo user and records seeded!")
        else:
            print("Demo user already exists.")

if __name__ == "__main__":
    asyncio.run(seed_data())
