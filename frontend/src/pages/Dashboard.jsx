import React, { useEffect, useState, useRef } from 'react';
import { Card, Row, Col, Statistic, Typography, Spin, List, message, Button, Form, Input, Select, InputNumber, Space, Empty, Tag, Popconfirm } from 'antd';
import { BookOutlined, ClockCircleOutlined, TrophyOutlined, RocketOutlined, RobotOutlined, UserOutlined, ArrowRightOutlined, TeamOutlined, CopyOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { dashboardService, agentService, parentLinkService } from '../services/api';
import EmptyGuide from '../components/EmptyGuide';
import { getRole } from '../services/session';

const { Title } = Typography;

// 模型输出里常带 Markdown 记号、序号、CoT 标题头，统一在这里洗掉。
const REC_JUNK = ['目标', '思考', '行动步骤', '下一步', '行动建议'];

const cleanRecText = (item) =>
  String(item || '')
    .replace(/^[#\s\d\.\-•*]+/, '')
    .replace(/^(目标|思考|行动步骤|结果\/下一步|下一步)[\s：:]*/, '')
    .trim();

/**
 * 一条推荐行动。
 *
 * 后端给 `route` 就说明这条能落到具体页面，做成可点的；
 * 给不出来就只显示文字——比强行跳一个不相关的页面好。
 */
const RecommendationItem = ({ index, item, onGo }) => {
  const [hover, setHover] = useState(false);
  const clickable = Boolean(item.route);

  return (
    <List.Item
      onClick={clickable ? () => onGo(item.route) : undefined}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        cursor: clickable ? 'pointer' : 'default',
        padding: '10px 12px',
        borderRadius: 8,
        background: clickable && hover ? 'rgba(216, 233, 255, 0.6)' : 'transparent',
        transition: 'background 0.15s ease',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}
    >
      <span style={{ flex: 1, color: clickable && hover ? '#1677ff' : 'inherit' }}>
        {index + 1}. {item.text}
      </span>
      {clickable && (
        <span
          style={{
            color: hover ? '#1677ff' : 'rgba(0, 0, 0, 0.45)',
            fontSize: 13,
            whiteSpace: 'nowrap',
            transition: 'color 0.15s ease',
          }}
        >
          {item.cta || '去处理'} <ArrowRightOutlined />
        </span>
      )}
    </List.Item>
  );
};

const Dashboard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  // 家长视图：已绑定的孩子列表 + 当前查看哪个
  const [children, setChildren] = useState([]);
  const [childId, setChildId] = useState(null);
  const [messageApi, contextHolder] = message.useMessage();
  const navigate = useNavigate();
  const isParent = getRole() === 'parent';

  const summaryControllerRef = useRef(null);
  // 家长看的是孩子的学情，学生看自己。用 ref 保存当前查看对象，
  // 避免 fetchSummary 的闭包里拿到上一次渲染的旧值。
  const childIdRef = useRef(null);

  const fetchSummary = async (signal) => {
    setLoading(true);
    try {
      const res = await dashboardService.getSummary({ signal, studentId: childIdRef.current });
      setData(res.data);
    } catch (e) {
      const name = e?.name || '';
      const msg = e?.message || '';
      if (name !== 'CanceledError' && !msg.includes('canceled')) {
        const detail = e?.response?.data?.detail;
        messageApi.error(detail ? `获取仪表盘失败：${detail}` : '获取仪表盘失败，请重新登录或稍后再试');
      }
    } finally {
      setLoading(false);
    }
  };

  

  const [form] = Form.useForm();
  const submitRealRecord = async (values) => {
    try {
      await dashboardService.createRecord(values);
      messageApi.success('已记录真实学习活动');
      form.resetFields();
      await fetchSummary();
    } catch (e) {
      messageApi.error('记录学习活动失败');
    }
  };

  useEffect(() => {
    // 不再用 didInitRef 去「只跑一次」：StrictMode 下首次 effect 会被清理函数 abort，
    // 若再用标记拦住第二次，就永远拿不到数据、页面一直停在 loading。
    // 用 AbortController 保证只有一个请求存活即可。
    const controller = new AbortController();
    summaryControllerRef.current = controller;

    // 家长要先知道「看哪个孩子」，再请求概览；没绑定时按自己查（会得到空数据，
    // 由下面的引导卡片提示去绑定，而不是假装有数据）。
    const init = async () => {
      if (isParent) {
        try {
          const { data: childData } = await parentLinkService.children();
          const items = childData.items || [];
          setChildren(items);
          if (items.length) {
            childIdRef.current = items[0].student_id;
            setChildId(items[0].student_id);
          }
        } catch {
          // 家长绑定列表读失败不该挡住整页，退化成看自己
        }
      }
      await fetchSummary(controller.signal);
    };
    init();
    return () => controller.abort();
  }, []);

  // 优先用后端给的结构化版本（带 route/cta）；老接口只回字符串时退化成纯文本。
  const recItems = (() => {
    const actions = data?.recommendation_actions;
    const raw = Array.isArray(actions) && actions.length
      ? actions
      : (data?.recommendations ?? []).map((text) => ({ text }));
    return raw
      .map((item) => ({ ...item, text: cleanRecText(item.text) }))
      .filter((item) => item.text && item.text.length >= 3 && !REC_JUNK.includes(item.text));
  })();

  // ---- 邀请家长 ----
  // 授权方是学生：码由学生生成，家长拿码才能绑。所以这个入口必须在学生能看到的地方，
  // 放在家长门户里学生根本进不去，等于没有。
  const [invite, setInvite] = useState(null);
  const [inviteLoading, setInviteLoading] = useState(false);
  const [viewers, setViewers] = useState([]);

  const loadViewers = async () => {
    try {
      const { data } = await parentLinkService.viewers();
      setViewers(data.items || []);
    } catch {
      // 家长账号调用会 403，静默即可，这块本来就不给家长看
    }
  };

  useEffect(() => {
    if (!isParent) loadViewers();
  }, []);

  const createInvite = async () => {
    setInviteLoading(true);
    try {
      const { data } = await parentLinkService.createInvite();
      setInvite(data);
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '生成邀请码失败');
    } finally {
      setInviteLoading(false);
    }
  };

  const copyCode = async () => {
    if (!invite?.code) return;
    try {
      await navigator.clipboard.writeText(invite.code);
      messageApi.success('邀请码已复制');
    } catch {
      messageApi.info(`邀请码：${invite.code}`);
    }
  };

  const revokeViewer = async (parentId) => {
    try {
      await parentLinkService.revoke(parentId);
      messageApi.success('已撤销该家长的查看权限');
      loadViewers();
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '撤销失败');
    }
  };

  return (
    <div>
      {contextHolder}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={2} style={{ margin: 0 }}>
          {isParent ? '孩子的学习概览' : '欢迎回来，同学！'}
        </Title>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {isParent && children.length > 1 && (
            <Select
              value={childId}
              style={{ minWidth: 160 }}
              options={children.map((c) => ({ value: c.student_id, label: c.name }))}
              onChange={(value) => {
                childIdRef.current = value;
                setChildId(value);
                if (summaryControllerRef.current) summaryControllerRef.current.abort();
                summaryControllerRef.current = new AbortController();
                fetchSummary(summaryControllerRef.current.signal);
              }}
            />
          )}
          <Button onClick={() => {
            if (summaryControllerRef.current) summaryControllerRef.current.abort();
            summaryControllerRef.current = new AbortController();
            fetchSummary(summaryControllerRef.current.signal);
          }}>刷新</Button>
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 50 }}>
          <Spin spinning size="large" />
        </div>
      ) : (
        <>
          <Row gutter={16} className="dash-stats" style={{ marginTop: 24 }}>
            <Col xs={12} sm={8}>
              <Card>
                <Statistic
                  title="正在学习的课程"
                  value={data?.active_courses ?? 0}
                  prefix={<BookOutlined />}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8}>
              <Card>
                <Statistic
                  title="学习时长"
                  value={data?.study_hours ?? 0}
                  precision={1}
                  prefix={<ClockCircleOutlined />}
                  suffix="小时"
                />
              </Card>
            </Col>
            <Col xs={12} sm={8}>
              <Card>
                <Statistic
                  title="获得成就"
                  value={data?.achievements ?? 0}
                  prefix={<TrophyOutlined />}
                />
              </Card>
            </Col>
        </Row>

          {/* 全新账号各项都是 0，与其摆一排「0」不如直接给下一步动作。
              家长看到的不该是「去生成计划」，而是去看孩子的报告。 */}
          {!loading &&
            data &&
            !data.active_courses &&
            !data.study_hours &&
            !data.achievements && (
              <Card style={{ marginTop: 16 }} variant="borderless">
                {isParent ? (
                  <EmptyGuide
                    compact
                    icon={<UserOutlined />}
                    title="还没有关联到孩子的学习数据"
                    description="在家长门户里选择孩子后，可以看到最近的学习时长、错题分布和阶段评估。"
                    action={
                      <Button type="primary" onClick={() => navigate('/parent')}>
                        打开家长门户
                      </Button>
                    }
                  />
                ) : (
                  <EmptyGuide
                    compact
                    icon={<RocketOutlined />}
                    title="还没有学习记录，从一份 7 天计划开始"
                    description="选一个想投的实习方向，系统会给出每天一个动作和可检查的交付物。完成后可以在这里看到学习时长和成就。"
                    action={
                      <Space wrap>
                        <Button type="primary" icon={<RocketOutlined />} onClick={() => navigate('/plan/internship')}>
                          生成我的 7 天计划
                        </Button>
                        <Button icon={<RobotOutlined />} onClick={() => navigate('/mentor')}>
                          先问问 AI 导师
                        </Button>
                      </Space>
                    }
                  />
                )}
              </Card>
            )}

        <Row gutter={16} style={{ marginTop: 24 }}>
          <Col span={24}>
            {/* 推荐行动是给学生本人的学习建议，家长视图下不展示，避免角色错位 */}
            {!isParent && (
            <Card
              title="推荐行动"
              extra={
                <Button
                  size="small"
                  type="link"
                  onClick={() => {
                    if (summaryControllerRef.current) summaryControllerRef.current.abort();
                    summaryControllerRef.current = new AbortController();
                    fetchSummary(summaryControllerRef.current.signal);
                  }}
                >
                  换一批
                </Button>
              }
            >
              {recItems.length ? (
                <List
                  dataSource={recItems}
                  renderItem={(item, idx) => (
                    <RecommendationItem index={idx} item={item} onGo={navigate} />
                  )}
                />
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="还没有可执行的建议，先记录一次学习活动"
                >
                  <Button type="primary" onClick={() => navigate('/plan/internship')}>
                    生成我的 7 天计划
                  </Button>
                </Empty>
              )}
            </Card>
            )}
          </Col>
        </Row>

        <Row gutter={16} style={{ marginTop: 24 }}>
          <Col xs={12} sm={8}>
            <Card>
              <Statistic
                title="最近一周活动数"
                value={data?.weekly_activity ?? 0}
              />
            </Card>
          </Col>
          <Col xs={12} sm={8}>
            <Card>
              <Statistic
                title="科目覆盖数"
                value={data?.subjects_count ?? 0}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card title="最近成绩趋势">
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(data?.score_trend ?? []).map((p, i) => (
                  <div key={i} style={{ padding: '4px 8px', background: 'rgba(216, 233, 255, 0.55)', borderRadius: 6 }}>
                    <span style={{ fontWeight: 500 }}>{p.score}</span>
                  </div>
                ))}
              </div>
            </Card>
          </Col>
        </Row>

        <Row gutter={16} style={{ marginTop: 24 }}>
          <Col span={24}>
            {/* 邀请家长：学生是授权方，所以入口在学生这边 */}
            {!isParent && (
            <Card title={<span><TeamOutlined style={{ marginRight: 6 }} />让家长看到你的进度</span>}>
              {invite ? (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space align="center">
                    <Tag
                      color="blue"
                      style={{ fontSize: 20, padding: '4px 14px', letterSpacing: 4, fontFamily: 'monospace' }}
                    >
                      {invite.code}
                    </Tag>
                    <Button icon={<CopyOutlined />} onClick={copyCode}>复制</Button>
                    <Button type="link" onClick={createInvite} loading={inviteLoading}>换一个</Button>
                  </Space>
                  <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 13 }}>
                    {invite.expires_in_days} 天内有效，只能用一次。家长在「家长门户」输入这个码就能看到你的学情。
                  </span>
                </Space>
              ) : (
                <Space direction="vertical" size={8}>
                  <span style={{ color: 'rgba(0,0,0,0.45)' }}>
                    生成一个邀请码给家长，他们绑定后才能看到你的学习报告。你随时可以撤销。
                  </span>
                  <Button type="primary" onClick={createInvite} loading={inviteLoading}>
                    生成邀请码
                  </Button>
                </Space>
              )}

              {viewers.length > 0 && (
                <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
                  <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 13, marginRight: 8 }}>正在查看你学情的账号：</span>
                  {viewers.map((v) => (
                    <Popconfirm
                      key={v.parent_id}
                      title={`撤销后 ${v.name} 将看不到你的学情`}
                      onConfirm={() => revokeViewer(v.parent_id)}
                      okText="撤销"
                      cancelText="取消"
                    >
                      <Tag color="default" style={{ cursor: 'pointer', marginBottom: 4 }}>
                        {v.name} ×
                      </Tag>
                    </Popconfirm>
                  ))}
                </div>
              )}
            </Card>
            )}
          </Col>
        </Row>

        <Row gutter={16} style={{ marginTop: 24 }}>
          <Col span={24}>
            {/* 学习活动由学生本人录入，家长只读 */}
            {!isParent && (
            <Card title="记录真实学习活动">
              <Form form={form} layout="inline" onFinish={submitRealRecord} style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                <Form.Item name="subject" rules={[{ required: true, message: '请输入学科' }]}> 
                  <Input placeholder="学科（如：数学）" style={{ width: 180 }} />
                </Form.Item>
                <Form.Item name="topic" rules={[{ required: true, message: '请输入主题' }]}> 
                  <Input placeholder="主题（如：函数概念）" style={{ width: 220 }} />
                </Form.Item>
                <Form.Item name="activity_type" rules={[{ required: true, message: '请选择类型' }]}> 
                  <Select placeholder="活动类型" style={{ width: 160 }} options={[
                    { value: 'quiz', label: '测验' },
                    { value: 'reading', label: '阅读' },
                    { value: 'discussion', label: '讨论' },
                    { value: 'exam', label: '考试' }
                  ]} />
                </Form.Item>
                <Form.Item name="score">
                  <InputNumber placeholder="分数" min={0} max={100} style={{ width: 120 }} />
                </Form.Item>
                <Form.Item>
                  <Button type="primary" htmlType="submit">提交记录</Button>
                </Form.Item>
              </Form>
            </Card>
            )}
          </Col>
        </Row>
      </>
    )}
  </div>
  );
};

export default Dashboard;
