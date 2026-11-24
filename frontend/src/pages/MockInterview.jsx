import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
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
import { AudioOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { pageAgentService, resumeService, workflowService } from '../services/api';
import { getTokenClaims } from '../services/session';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

const ROUNDS = 4;

/* 正在进行的面试 id 按「账号」存在本地：答到一半刷新、关掉页面明天回来都能接着答。
   服务端只认 run 归属，本地这份丢了最多是重开一场，不会看到别人的记录。 */
const runKey = () => {
  const who = getTokenClaims()?.sub || 'anon';
  return `qicheng:mock_interview:${who}`;
};

function readRunId() {
  try {
    return localStorage.getItem(runKey()) || null;
  } catch {
    return null;
  }
}

function saveRunId(id) {
  try {
    if (id) localStorage.setItem(runKey(), id);
    else localStorage.removeItem(runKey());
  } catch {
    // 隐私模式下不可写，退化成「刷新后重开」，不影响面试本身
  }
}

/**
 * 模拟面试：跑在可恢复的状态机上（edu_app/flows/mock_interview.py）。
 *
 * 页面只做三件事：收集输入、把这一轮的回答交给服务端、渲染 run.state。
 * 出题、评分、改写建议全在服务端，且每一步都落库——关掉页面明天回来
 * 还能接着答，这是这个页面和「一次请求跑完」的功能最本质的区别。
 */
const MockInterview = () => {
  const [resumes, setResumes] = useState([]);
  const [resumeId, setResumeId] = useState(undefined);
  const [role, setRole] = useState('');
  const [jd, setJd] = useState('');
  const [run, setRun] = useState(null);
  const [answer, setAnswer] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    resumeService
      .list()
      .then(({ data }) => {
        const items = data.items || [];
        setResumes(items);
        if (items.length) {
          setResumeId(items[0].id);
          setRole(items[0].target_role || '');
        }
      })
      .catch(() => setError('简历列表读取失败，刷新试试'));
  }, []);

  // 刷新恢复：本地存着 run id 就把它接回来。
  // 已结束的（succeeded）也恢复，让用户还能看到总评；作废/失败的清掉本地记录。
  useEffect(() => {
    const stored = readRunId();
    if (!stored) return undefined;
    let cancelled = false;
    workflowService
      .get(stored)
      .then(({ data }) => {
        if (cancelled) return;
        if (['running', 'awaiting_human', 'succeeded'].includes(data.status)) {
          setRun(data);
        } else {
          saveRunId(null);
        }
      })
      .catch(() => {
        // 404 说明流程已被清掉，本地这份就是过期 id
        if (!cancelled) saveRunId(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 只有「还能继续或还值得看」的状态才留 id，其余清掉，避免下次刷新接回一个死流程
  const syncStored = (data) => {
    const keep = ['running', 'awaiting_human', 'succeeded'].includes(data?.status);
    saveRunId(keep ? data.id : null);
  };

  const state = run?.state || {};
  const pending = state._pending || {};
  const answers = state.answers || [];
  const review = state.review || null;
  const questions = state.questions || [];

  const start = async () => {
    setLoading(true);
    setError('');
    setRun(null);
    setAnswer('');
    saveRunId(null);
    try {
      const { data } = await workflowService.start('mock_interview', {
        resume_id: resumeId,
        target_role: role.trim() || undefined,
        jd_text: jd.trim() || undefined,
      });
      setRun(data);
      syncStored(data);
      if (data.status === 'failed') setError(data.error || '流程执行失败');
    } catch (e) {
      setError(e?.response?.data?.detail || '开始面试失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  const submitAnswer = async () => {
    if (!run?.id) return;
    if (answer.trim().length < 4) {
      setError('至少写一句话，空回答不计分');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const { data } = await workflowService.resume(run.id, true, { answer: answer.trim() });
      setRun(data);
      syncStored(data);
      setAnswer('');
      if (data.status === 'failed') setError(data.error || '流程执行失败');
    } catch (e) {
      setError(e?.response?.data?.detail || '提交失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const finish = async () => {
    if (!run?.id) return;
    setLoading(true);
    try {
      const { data } = await workflowService.resume(run.id, false);
      setRun(data);
      syncStored(data);
    } catch (e) {
      setError(e?.response?.data?.detail || '操作失败');
    } finally {
      setLoading(false);
    }
  };

  const decideProposal = async (accept) => {
    const proposalId = pending.proposal_id;
    if (!proposalId) return;
    setLoading(true);
    setError('');
    try {
      const { data } = await pageAgentService.confirm(proposalId, accept);
      if (data.run) {
        setRun(data.run);
        syncStored(data.run);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || '操作失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const stepItems = useMemo(() => {
    const done = new Set(answers.map((item) => item.index));
    const total = questions.length || ROUNDS;
    const current = pending.kind === 'answer' ? pending.index : null;
    const items = Array.from({ length: total }, (_, i) => {
      const index = i + 1;
      let status = 'wait';
      if (done.has(index)) status = 'finish';
      else if (current === index) status = 'process';
      return { title: `第 ${index} 题`, status };
    });
    items.push({
      title: '总评',
      status: review ? 'finish' : pending.kind === 'proposal' ? 'finish' : 'wait',
    });
    return items;
  }, [answers, questions.length, pending, review]);

  const preview = pending.preview || {};
  const score = Number(review?.score ?? 0);

  return (
    <div style={{ padding: 24, maxWidth: 1180, margin: '0 auto' }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        模拟面试
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 20 }}>
        我出题、你回答，{ROUNDS} 轮之后给总评。每一轮都会落库，关掉页面明天回来还能接着答。
        答完我会提一条简历改写建议，你确认了才会动你的简历。
      </Paragraph>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

      <Row gutter={20}>
        <Col xs={24} lg={9}>
          <Card title="面试设置" size="small">
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  用哪份简历
                </Text>
                <Select
                  style={{ width: '100%', marginTop: 4 }}
                  placeholder={resumes.length ? '选择简历' : '还没有简历'}
                  value={resumeId}
                  onChange={(value) => {
                    setResumeId(value);
                    const hit = resumes.find((item) => item.id === value);
                    if (hit?.target_role) setRole(hit.target_role);
                  }}
                  options={resumes.map((item) => ({ value: item.id, label: item.title }))}
                />
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  目标岗位
                </Text>
                <Input
                  style={{ marginTop: 4 }}
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  placeholder="例如：B 端产品经理"
                />
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  岗位描述（可选，填了问题会更贴岗位）
                </Text>
                <TextArea
                  style={{ marginTop: 4 }}
                  rows={5}
                  value={jd}
                  onChange={(e) => setJd(e.target.value)}
                  placeholder="把招聘要求粘进来"
                />
              </div>
              <Button
                type="primary"
                icon={<AudioOutlined />}
                loading={loading}
                onClick={start}
                block
              >
                {run ? '重新开始一场' : '开始面试'}
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={15}>
          <Card title="面试" size="small">
            {!run && !loading && (
              <Empty description="还没有开始" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
            {loading && !run && <Text type="secondary">正在读简历、设计问题…</Text>}

            {run && (
              <Space direction="vertical" style={{ width: '100%' }} size={16}>
                <Steps size="small" items={stepItems} />

                {pending.kind === 'answer' && (
                  <>
                    <div>
                      <Space align="baseline">
                        <Tag color="blue">
                          第 {pending.index} / {pending.total || ROUNDS} 题
                        </Tag>
                        {pending.focus && <Text type="secondary" style={{ fontSize: 12 }}>{pending.focus}</Text>}
                      </Space>
                      <Paragraph style={{ marginTop: 8, marginBottom: 0, fontSize: 15 }}>
                        {pending.question}
                      </Paragraph>
                    </div>
                    <div>
                      <TextArea
                        rows={7}
                        value={answer}
                        onChange={(e) => setAnswer(e.target.value)}
                        placeholder={pending.hint || '用 3~6 句话说清背景、你做了什么、结果如何'}
                        disabled={loading}
                      />
                      <Space style={{ marginTop: 10 }}>
                        <Button
                          type="primary"
                          loading={loading}
                          onClick={submitAnswer}
                          disabled={!answer.trim()}
                        >
                          提交回答
                        </Button>
                        <Button onClick={finish} disabled={loading}>
                          结束这场面试
                        </Button>
                      </Space>
                    </div>
                  </>
                )}

                {pending.kind === 'proposal' && (
                  <>
                    <Alert
                      type="info"
                      showIcon
                      message="面试结束。我建议改这一段简历，你确认后才会写入"
                      description={pending.why || preview.note}
                    />
                    <div>
                      <Text strong style={{ fontSize: 13 }}>
                        {preview.title || pending.title}
                      </Text>
                      <Row gutter={12} style={{ marginTop: 8 }}>
                        <Col span={12}>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            现在
                          </Text>
                          <List
                            size="small"
                            dataSource={preview.before || []}
                            locale={{ emptyText: '（空）' }}
                            renderItem={(item) => (
                              <List.Item style={{ paddingLeft: 0, fontSize: 13 }}>{item}</List.Item>
                            )}
                          />
                        </Col>
                        <Col span={12}>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            改成
                          </Text>
                          <List
                            size="small"
                            dataSource={preview.after || []}
                            renderItem={(item) => (
                              <List.Item style={{ paddingLeft: 0, fontSize: 13 }}>{item}</List.Item>
                            )}
                          />
                        </Col>
                      </Row>
                    </div>
                    <Space>
                      <Button
                        type="primary"
                        icon={<CheckOutlined />}
                        loading={loading}
                        onClick={() => decideProposal(true)}
                      >
                        应用到简历
                      </Button>
                      <Button
                        icon={<CloseOutlined />}
                        disabled={loading}
                        onClick={() => decideProposal(false)}
                      >
                        不用了
                      </Button>
                    </Space>
                  </>
                )}

                {run.status === 'succeeded' && (
                  <>
                    <div>
                      <Space align="baseline">
                        <Text strong>面试得分</Text>
                        <Text type="secondary">{score}</Text>
                      </Space>
                      <Progress
                        percent={score}
                        strokeColor={score >= 70 ? '#3f8600' : '#d46b08'}
                        style={{ marginTop: 6 }}
                      />
                      {review?.verdict && (
                        <Paragraph style={{ marginTop: 8, marginBottom: 0 }}>
                          {review.verdict}
                        </Paragraph>
                      )}
                    </div>

                    {(review?.strengths || []).length > 0 && (
                      <div>
                        <Text strong style={{ fontSize: 13 }}>
                          答得好的地方
                        </Text>
                        <List
                          size="small"
                          dataSource={review.strengths}
                          renderItem={(item) => (
                            <List.Item style={{ paddingLeft: 0, fontSize: 13 }}>
                              <Tag color="green">亮点</Tag>
                              {item}
                            </List.Item>
                          )}
                        />
                      </div>
                    )}

                    {(review?.issues || []).length > 0 && (
                      <div>
                        <Text strong style={{ fontSize: 13 }}>
                          需要改的地方
                        </Text>
                        <List
                          size="small"
                          dataSource={review.issues}
                          renderItem={(item) => (
                            <List.Item style={{ paddingLeft: 0, alignItems: 'flex-start' }}>
                              <div>
                                <Text style={{ fontSize: 13 }}>
                                  {item.index ? `第 ${item.index} 题：` : ''}
                                  {item.issue}
                                </Text>
                                {item.better && (
                                  <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 2 }}>
                                    更好的答法：{item.better}
                                  </div>
                                )}
                              </div>
                            </List.Item>
                          )}
                        />
                      </div>
                    )}

                    {(review?.next_actions || []).length > 0 && (
                      <div>
                        <Text strong style={{ fontSize: 13 }}>
                          下一场之前
                        </Text>
                        <List
                          size="small"
                          dataSource={review.next_actions}
                          renderItem={(item, index) => (
                            <List.Item style={{ paddingLeft: 0, fontSize: 13 }}>
                              {index + 1}. {item}
                            </List.Item>
                          )}
                        />
                      </div>
                    )}

                    {state.improvement_note && (
                      <Alert type="warning" showIcon message={state.improvement_note} />
                    )}
                  </>
                )}

                {run.status === 'cancelled' && (
                  <Alert type="warning" showIcon message="这场面试已结束，没有写入任何改动" />
                )}

                {run.status === 'failed' && (
                  <Alert
                    type="error"
                    showIcon
                    message="流程失败"
                    description={run.error || '未知原因'}
                  />
                )}

                {answers.length > 0 && (
                  <>
                    <Divider style={{ margin: '4px 0' }} />
                    <div>
                      <Text strong style={{ fontSize: 13 }}>
                        已回答（{answers.length}）
                      </Text>
                      <List
                        size="small"
                        dataSource={answers}
                        renderItem={(item) => (
                          <List.Item style={{ paddingLeft: 0, alignItems: 'flex-start' }}>
                            <div>
                              <Text style={{ fontSize: 13 }}>第 {item.index} 题：{item.question}</Text>
                              <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 2 }}>
                                {item.answer || '（未作答）'}
                              </div>
                            </div>
                          </List.Item>
                        )}
                      />
                    </div>
                  </>
                )}
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default MockInterview;
