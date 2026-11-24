import React, { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  List,
  Progress,
  Row,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
} from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import { resumeService, workflowService } from '../services/api';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

const STEP_LABEL = [
  ['load_resume', '读取简历'],
  ['extract_jd', '拆解 JD'],
  ['match_requirements', '核对要求'],
  ['advise', '生成建议'],
];

/**
 * JD 匹配：跑在可恢复的状态机上（edu_app/flows/jd_match.py）。
 *
 * 页面只做三件事：收集输入、触发流程、把 run.state 渲染出来。
 * 判断逻辑全在服务端——尤其是覆盖率，必须由代码算，同一份 JD 问两次要给同一结论。
 */
const JdMatch = () => {
  const [resumes, setResumes] = useState([]);
  const [resumeId, setResumeId] = useState(undefined);
  const [jd, setJd] = useState('');
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    resumeService
      .list()
      .then(({ data }) => {
        const items = data.items || [];
        setResumes(items);
        if (items.length) setResumeId(items[0].id);
      })
      .catch(() => setError('简历列表读取失败，刷新试试'));
  }, []);

  const start = async () => {
    if (jd.trim().length < 40) {
      setError('JD 至少粘 40 个字，否则拆不出有效要求');
      return;
    }
    setLoading(true);
    setError('');
    setRun(null);
    try {
      const { data } = await workflowService.start('jd_match', {
        jd_text: jd.trim(),
        resume_id: resumeId,
      });
      setRun(data);
      if (data.status === 'failed') setError(data.error || '流程执行失败');
    } catch (e) {
      setError(e?.response?.data?.detail || '匹配失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  const decide = async (accept) => {
    if (!run?.id) return;
    setLoading(true);
    try {
      const { data } = await workflowService.resume(run.id, accept);
      setRun(data);
    } catch (e) {
      setError(e?.response?.data?.detail || '操作失败');
    } finally {
      setLoading(false);
    }
  };

  const doneMap = {};
  (run?.steps || []).forEach((item) => {
    doneMap[item.step] = item.status;
  });
  const stepItems = STEP_LABEL.map(([key, label]) => {
    let status = 'wait';
    if (doneMap[key] === 'succeeded') status = 'finish';
    else if (doneMap[key] === 'failed') status = 'error';
    else if (run?.current_step === key && run?.status === 'running') status = 'process';
    return { title: label, status };
  });

  const state = run?.state || {};
  const match = state.match || {};
  const advice = state.advice || [];
  const requirements = state.requirements || {};
  const coverage = Math.round(Number(match.coverage || 0) * 100);

  return (
    <div style={{ padding: 24, maxWidth: 1180, margin: '0 auto' }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        JD 匹配
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 20 }}>
        把招聘要求粘进来，对照你的简历看差在哪。核对由代码完成——同一份 JD 和简历永远给同一个结论，
        模型只负责拆解要求和给建议。
      </Paragraph>

      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
      )}

      <Row gutter={20}>
        <Col xs={24} lg={10}>
          <Card title="输入" size="small">
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  用哪份简历
                </Text>
                <Select
                  style={{ width: '100%', marginTop: 4 }}
                  placeholder={resumes.length ? '选择简历' : '还没有简历'}
                  value={resumeId}
                  onChange={setResumeId}
                  options={resumes.map((item) => ({ value: item.id, label: item.title }))}
                />
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  岗位描述（JD）
                </Text>
                <TextArea
                  style={{ marginTop: 4 }}
                  rows={12}
                  value={jd}
                  onChange={(e) => setJd(e.target.value)}
                  placeholder="把招聘网站上的岗位职责与要求整段粘进来，越完整越准"
                />
              </div>
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                loading={loading}
                onClick={start}
                block
              >
                {loading ? '正在跑流程…' : '开始匹配'}
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title="结果" size="small">
            {!run && !loading && (
              <Empty description="还没有结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
            {loading && !run && <Text type="secondary">正在读取简历、拆解 JD、核对要求…</Text>}

            {run && (
              <Space direction="vertical" style={{ width: '100%' }} size={16}>
                <Steps size="small" items={stepItems} />

                {run.status === 'awaiting_human' && (
                  <Alert
                    type="warning"
                    showIcon
                    message="这一步需要你确认"
                    description={(state._pending || {}).summary}
                    action={
                      <Space>
                        <Button size="small" onClick={() => decide(false)}>
                          放弃
                        </Button>
                        <Button size="small" type="primary" onClick={() => decide(true)}>
                          继续
                        </Button>
                      </Space>
                    }
                  />
                )}

                {run.status === 'succeeded' && (
                  <>
                    <div>
                      <Space align="baseline">
                        <Text strong>硬性要求命中率</Text>
                        <Text type="secondary">{coverage}%</Text>
                        {requirements.role && <Tag color="blue">{requirements.role}</Tag>}
                      </Space>
                      <Progress
                        percent={coverage}
                        strokeColor={coverage >= 70 ? '#3f8600' : '#d46b08'}
                        style={{ marginTop: 6 }}
                      />
                    </div>

                    <Row gutter={16}>
                      <Col span={12}>
                        <Text strong style={{ fontSize: 13 }}>
                          已命中（{(match.matched || []).length}）
                        </Text>
                        <List
                          size="small"
                          dataSource={match.matched || []}
                          locale={{ emptyText: '没有命中项' }}
                          renderItem={(item) => (
                            <List.Item style={{ paddingLeft: 0 }}>
                              <Tag color="green">命中</Tag>
                              <Text style={{ fontSize: 13 }}>{item.text}</Text>
                            </List.Item>
                          )}
                        />
                      </Col>
                      <Col span={12}>
                        <Text strong style={{ fontSize: 13 }}>
                          缺失（{(match.missing || []).length}）
                        </Text>
                        <List
                          size="small"
                          dataSource={match.missing || []}
                          locale={{ emptyText: '没有缺失项' }}
                          renderItem={(item) => (
                            <List.Item style={{ paddingLeft: 0 }}>
                              <Tag color="red">缺</Tag>
                              <Text style={{ fontSize: 13 }}>{item.text}</Text>
                            </List.Item>
                          )}
                        />
                      </Col>
                    </Row>

                    {(match.unverified || []).length > 0 && (
                      <div>
                        <Text strong style={{ fontSize: 13 }}>
                          无法从简历判断
                        </Text>
                        <div style={{ marginTop: 6 }}>
                          {(match.unverified || []).map((item, index) => (
                            <div key={index} style={{ fontSize: 13, color: '#8c8c8c' }}>
                              · {item.text}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div>
                      <Text strong style={{ fontSize: 13 }}>
                        怎么补
                      </Text>
                      <List
                        size="small"
                        dataSource={advice}
                        renderItem={(item, index) => (
                          <List.Item style={{ paddingLeft: 0 }}>
                            <Text style={{ fontSize: 13 }}>
                              {index + 1}. {item}
                            </Text>
                          </List.Item>
                        )}
                      />
                    </div>
                  </>
                )}

                {run.status === 'failed' && (
                  <Alert
                    type="error"
                    showIcon
                    message="流程失败"
                    description={run.error || '未知原因'}
                  />
                )}
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default JdMatch;
