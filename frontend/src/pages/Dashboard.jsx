import React, { useEffect, useState, useRef } from 'react';
import { Card, Row, Col, Statistic, Typography, Spin, List, message, Button, Form, Input, Select, InputNumber, Space } from 'antd';
import { BookOutlined, ClockCircleOutlined, TrophyOutlined, RocketOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { dashboardService, agentService } from '../services/api';
import EmptyGuide from '../components/EmptyGuide';
import { getRole } from '../services/session';

const { Title } = Typography;

const Dashboard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const navigate = useNavigate();
  const isParent = getRole() === 'parent';

  const summaryControllerRef = useRef(null);

  const fetchSummary = async (signal) => {
    setLoading(true);
    try {
      const res = await dashboardService.getSummary({ signal });
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
    fetchSummary(controller.signal);
    return () => controller.abort();
  }, []);

  return (
    <div>
      {contextHolder}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={2} style={{ margin: 0 }}>
          {isParent ? '孩子的学习概览' : '欢迎回来，同学！'}
        </Title>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
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
            <Card title="推荐行动">
              <List
                dataSource={(data?.recommendations ?? [])
                  .map(item => String(item || '').replace(/^[#\s\d\.\-•*]+/, '').replace(/^(目标|思考|行动步骤|结果\/下一步|下一步)[\s：:]*/, '').trim())
                  .filter(item => item && item.length >= 3 && !['目标', '思考', '行动步骤', '下一步', '行动建议'].includes(item))
                }
                renderItem={(item, idx) => (
                  <List.Item>
                    <span>{idx + 1}. {item}</span>
                  </List.Item>
                )}
              />
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
