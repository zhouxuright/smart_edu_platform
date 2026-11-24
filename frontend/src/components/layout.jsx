import React, { useState, useMemo } from 'react';
import { Layout, Menu, Button, Tag, Drawer, Grid, message } from 'antd';
import {
  HomeOutlined,
  RocketOutlined,
  RobotOutlined,
  BookOutlined,
  BarChartOutlined,
  TeamOutlined,
  UserOutlined,
  LogoutOutlined,
  MenuOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  SendOutlined,
  FileTextOutlined,
  AimOutlined,
  AudioOutlined,
} from '@ant-design/icons';
import { useNavigate, Outlet, useLocation } from 'react-router-dom';
import { authService } from '../services/api';
import { getRole, getDisplayName, getRoleLabel } from '../services/session';
import NotificationBell from './NotificationBell';
import AgentDock from './AgentDock';

const { Header, Content, Sider } = Layout;
const { useBreakpoint } = Grid;

/**
 * 菜单配置。
 *
 * roles 决定谁能看到这一项：学生看不到家长门户，家长看不到学习内容，
 * 管理员全都能看到。新增页面时记得在这里登记，否则导航里不会出现。
 */
export const MENU_ITEMS = [
  {
    key: '/dashboard',
    icon: <HomeOutlined />,
    label: '仪表盘',
    roles: ['student', 'parent', 'admin'],
    group: '学习',
  },
  {
    key: '/plan/internship',
    icon: <RocketOutlined />,
    label: '我的计划',
    roles: ['student', 'admin'],
    group: '学习',
  },
  {
    key: '/mentor',
    icon: <RobotOutlined />,
    label: 'AI 学习导师',
    roles: ['student', 'admin'],
    group: '学习',
  },
  {
    key: '/knowledge',
    icon: <BookOutlined />,
    label: '知识图谱',
    roles: ['student', 'admin'],
    group: '内容',
  },
  {
    key: '/mistake-book',
    icon: <BookOutlined />,
    label: '智能错题本',
    roles: ['student', 'admin'],
    group: '内容',
  },
  {
    key: '/knowledge-extraction',
    icon: <ExperimentOutlined />,
    label: '多学科知识抽取',
    roles: ['student', 'admin'],
    group: '内容',
  },
  {
    key: '/rag',
    icon: <DatabaseOutlined />,
    label: '企业知识库',
    roles: ['student', 'admin'],
    group: '内容',
  },
  {
    key: '/resume',
    icon: <FileTextOutlined />,
    label: '简历工作台',
    roles: ['student', 'admin'],
    group: '求职',
  },
  {
    key: '/jd-match',
    icon: <AimOutlined />,
    label: 'JD 匹配',
    roles: ['student', 'admin'],
    group: '求职',
  },
  {
    key: '/mock-interview',
    icon: <AudioOutlined />,
    label: '模拟面试',
    roles: ['student', 'admin'],
    group: '求职',
  },
  {
    key: '/applications',
    icon: <SendOutlined />,
    label: '投递看板',
    roles: ['student', 'admin'],
    group: '求职',
  },
  {
    key: '/assessment',
    icon: <BarChartOutlined />,
    label: '学习评估',
    roles: ['student', 'parent', 'admin'],
    group: '成长',
  },
  {
    key: '/collaborative',
    icon: <TeamOutlined />,
    label: '协作学习',
    roles: ['student', 'admin'],
    group: '成长',
  },
  {
    key: '/parent',
    icon: <UserOutlined />,
    label: '家长门户',
    roles: ['parent', 'admin'],
    group: '成长',
  },
];

const GROUP_ORDER = ['学习', '内容', '求职', '成长'];

/** 按当前角色筛出可见菜单，并保持分组顺序 */
export function buildMenuItems(role) {
  const visible = MENU_ITEMS.filter((item) => item.roles.includes(role));
  const groups = GROUP_ORDER.filter((g) => visible.some((i) => i.group === g));

  // 只有一组时不必分组，避免单个分组标题显得啰嗦
  if (groups.length <= 1) {
    return visible.map(({ key, icon, label }) => ({ key, icon, label }));
  }

  return groups.map((group) => ({
    key: `group-${group}`,
    type: 'group',
    label: group,
    children: visible
      .filter((i) => i.group === group)
      .map(({ key, icon, label }) => ({ key, icon, label })),
  }));
}

const PAGE_TITLE = {
  '/dashboard': '仪表盘',
  '/plan/internship': '我的计划',
  '/mentor': 'AI 学习导师',
  '/knowledge': '知识图谱',
  '/mistake-book': '智能错题本',
  '/knowledge-extraction': '多学科知识抽取',
  '/rag': '企业知识库',
  '/applications': '投递看板',
  '/resume': '简历工作台',
  '/jd-match': 'JD 匹配',
  '/mock-interview': '模拟面试',
  '/assessment': '学习评估',
  '/collaborative': '协作学习',
  '/parent': '家长门户',
};

/**
 * 移动端底部标签栏。
 *
 * 手机上侧边抽屉要两步才能到达目标页，底部标签一步到位；
 * 只放最高频的几项，其余仍走抽屉，避免标签挤成一排小字。
 */
const TABS = {
  student: [
    { key: '/dashboard', label: '首页', icon: <HomeOutlined /> },
    { key: '/plan/internship', label: '计划', icon: <RocketOutlined /> },
    { key: '/mentor', label: '导师', icon: <RobotOutlined /> },
    { key: '/resume', label: '简历', icon: <FileTextOutlined /> },
    { key: '/applications', label: '投递', icon: <SendOutlined /> },
  ],
  parent: [
    { key: '/dashboard', label: '首页', icon: <HomeOutlined /> },
    { key: '/assessment', label: '评估', icon: <BarChartOutlined /> },
    { key: '/parent', label: '家长', icon: <UserOutlined /> },
  ],
};

TABS.admin = TABS.student;

const MainLayout = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const screens = useBreakpoint();
  const isMobile = !screens.lg;
  const [messageApi, contextHolder] = message.useMessage();

  const role = getRole();
  const items = useMemo(() => buildMenuItems(role), [role]);
  const tabs = TABS[role] || TABS.student;
  const userName = getDisplayName();

  const handleLogout = () => {
    authService.logout();
    navigate('/login');
    messageApi.success('已退出登录');
  };

  const go = ({ key }) => {
    if (String(key).startsWith('group-')) return;
    navigate(key);
    setDrawerOpen(false);
  };

  const brand = (compact) => (
    <div className="app-brand">
      <span className="app-brand-mark">启</span>
      {!compact && <span>启程 AI</span>}
    </div>
  );

  const menu = (
    <Menu
      mode="inline"
      items={items}
      onClick={go}
      selectedKeys={[location.pathname]}
      style={{ background: 'transparent', borderInlineEnd: 'none' }}
    />
  );

  return (
    <Layout style={{ minHeight: '100vh', background: 'transparent' }}>
      {contextHolder}

      {!isMobile && (
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          width={228}
          style={{
            borderRight: '1px solid var(--glass-border)',
            background: 'rgba(255, 255, 255, 0.66)',
            backdropFilter: 'blur(14px)',
          }}
        >
          {brand(collapsed)}
          {menu}
        </Sider>
      )}

      {isMobile && (
        <Drawer
          placement="left"
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          width={240}
          styles={{ body: { padding: 0, background: 'rgba(255,255,255,0.96)' } }}
          closable={false}
        >
          {brand(false)}
          {menu}
        </Drawer>
      )}

      <Layout style={{ background: 'transparent' }}>
        <Header className="app-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
            {isMobile && (
              <Button
                type="text"
                icon={<MenuOutlined />}
                onClick={() => setDrawerOpen(true)}
                aria-label="打开导航"
              />
            )}
            <span className="app-header-title">
              {PAGE_TITLE[location.pathname] || '启程 AI'}
            </span>
          </div>

          <div className="app-header-user">
            <NotificationBell />
            {!isMobile && userName && <span>{userName}</span>}
            {!isMobile && (
              <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                {getRoleLabel()}
              </Tag>
            )}
            <Button type="text" icon={<LogoutOutlined />} onClick={handleLogout} aria-label="退出登录">
              {!isMobile && '退出'}
            </Button>
          </div>
        </Header>

        <Content style={{ margin: isMobile ? 12 : 20, paddingBottom: isMobile ? 68 : 0 }}>
          <div className="glass-card" style={{ padding: isMobile ? 16 : 24, minHeight: 360 }}>
            <Outlet />
          </div>
        </Content>
      </Layout>

      {isMobile && (
        <nav className="app-tabbar" aria-label="底部导航">
          {tabs.map((tab) => {
            const active = location.pathname === tab.key
              || (tab.key !== '/dashboard' && location.pathname.startsWith(tab.key));
            return (
              <button
                key={tab.key}
                type="button"
                className={active ? 'app-tab is-active' : 'app-tab'}
                aria-current={active ? 'page' : undefined}
                onClick={() => navigate(tab.key)}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            );
          })}
        </nav>
      )}

      {/* 导师页本身就是一整块对话，再挂一个 dock 会变成两个输入框 */}
      {!location.pathname.startsWith('/mentor') && <AgentDock />}
    </Layout>
  );
};

export default MainLayout;
