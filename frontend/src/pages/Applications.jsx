import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button, Card, Col, DatePicker, Form, Input, Modal, Popconfirm, Row,
  Segmented, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
  ArrowRightOutlined, ClockCircleOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { applicationService } from '../services/api';
import { useAgentApplied } from '../services/agentPage';
import EmptyGuide from '../components/EmptyGuide';
import { track } from '../services/telemetry';

const { Text, Title } = Typography;

// 状态元信息：颜色沿用「涨红跌绿」之外的中性语义，避免和成绩类指标混淆
const STATUS_META = {
  draft: { label: '待投', color: 'default' },
  applied: { label: '已投递', color: 'blue' },
  screening: { label: '笔试/筛选', color: 'geekblue' },
  interview: { label: '面试中', color: 'gold' },
  offer: { label: '已拿 offer', color: 'green' },
  rejected: { label: '未通过', color: 'red' },
  withdrawn: { label: '已撤回', color: 'default' },
};

// 与后端 TRANSITIONS 保持一致：只给合法选项，避免用户点了报错
const NEXT_STATUS = {
  draft: ['applied', 'withdrawn'],
  applied: ['screening', 'interview', 'rejected', 'withdrawn'],
  screening: ['interview', 'rejected', 'withdrawn'],
  interview: ['offer', 'rejected', 'withdrawn'],
  offer: ['withdrawn'],
  rejected: ['applied'],
  withdrawn: ['applied'],
};

const CHANNEL_LABEL = {
  referral: '内推',
  campus: '校招',
  job_board: '招聘平台',
  company_site: '官网投递',
  other: '其他',
};

const CLOSED = new Set(['offer', 'rejected', 'withdrawn']);

const isOverdue = (item) =>
  item.next_action_at && !CLOSED.has(item.status) && dayjs(item.next_action_at).isBefore(dayjs());

const Applications = () => {
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState('board');
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [listRes, statsRes] = await Promise.all([
        applicationService.list(),
        applicationService.stats(),
      ]);
      setItems(listRes.data.items || []);
      setStats(statsRes.data);
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '加载投递记录失败');
    } finally {
      setLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    load();
  }, [load]);

  // Agent 推进状态后由它广播，这里重拉列表与漏斗统计
  useAgentApplied(({ tool }) => {
    if (tool !== 'advance_application') return;
    load();
    messageApi.success('状态已更新');
  });

  const openCreate = () => setEditing({ mode: 'create' });

  const openEdit = (item) => setEditing({ mode: 'edit', item });

  // 弹窗常驻挂载（forceRender），字段在这里注入。
  // 若用 destroyOnClose，Form 会被卸载，useForm 实例失联、setFieldsValue 静默失效。
  useEffect(() => {
    if (!editing) return;
    if (editing.mode === 'create') {
      form.resetFields();
      form.setFieldsValue({ status: 'draft', channel: 'job_board' });
    } else {
      const item = editing.item;
      form.setFieldsValue({
        ...item,
        next_action_at: item.next_action_at ? dayjs(item.next_action_at) : null,
      });
    }
  }, [editing, form]);

  const submit = async (values) => {
    setSaving(true);
    const payload = {
      ...values,
      next_action_at: values.next_action_at ? values.next_action_at.toISOString() : null,
    };
    try {
      if (editing.mode === 'create') {
        await applicationService.create(payload);
        track('application_create', { channel: payload.channel, status: payload.status });
        messageApi.success('已添加投递记录');
      } else {
        await applicationService.update(editing.item.id, payload);
        track('application_update', { status: payload.status });
        messageApi.success('已更新');
      }
      setEditing(null);
      await load();
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const advance = async (item, status) => {
    try {
      await applicationService.update(item.id, { status });
      track('application_advance', { from: item.status, to: status });
      messageApi.success(`已更新为「${STATUS_META[status].label}」`);
      await load();
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '更新失败');
    }
  };

  const remove = async (item) => {
    try {
      await applicationService.remove(item.id);
      messageApi.success('已删除');
      await load();
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const grouped = useMemo(() => {
    const map = {};
    Object.keys(STATUS_META).forEach((s) => { map[s] = []; });
    items.forEach((it) => {
      (map[it.status] || (map[it.status] = [])).push(it);
    });
    return map;
  }, [items]);

  // 编辑时的状态可选项：当前状态 + 合法下一状态，避免下拉里出现非法组合
  const statusOptions = useMemo(() => {
    if (editing?.mode !== 'edit') {
      return [{ value: 'draft', label: '待投' }, { value: 'applied', label: '已投递' }];
    }
    const allowed = [editing.item.status, ...(NEXT_STATUS[editing.item.status] || [])];
    return [...new Set(allowed)].map((s) => ({ value: s, label: STATUS_META[s].label }));
  }, [editing]);

  const renderCard = (item) => {
    const overdue = isOverdue(item);
    const nexts = NEXT_STATUS[item.status] || [];
    return (
      <Card
        key={item.id}
        size="small"
        style={{ marginBottom: 10 }}
        styles={{ body: { padding: 12 } }}
        title={
          <Space size={6} style={{ minWidth: 0 }}>
            <Text strong ellipsis={{ tooltip: item.company }} style={{ maxWidth: 150 }}>
              {item.company}
            </Text>
          </Space>
        }
        extra={
          <Space size={2}>
            <Tooltip title="编辑">
              <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(item)} />
            </Tooltip>
            <Popconfirm title="删除这条投递记录？" okText="删除" cancelText="取消" onConfirm={() => remove(item)}>
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        }
      >
        <Text type="secondary" style={{ display: 'block', fontSize: 13, marginBottom: 8 }}>
          {item.position}
        </Text>
        <Space size={4} wrap>
          <Tag style={{ marginInlineEnd: 0 }}>{CHANNEL_LABEL[item.channel] || item.channel}</Tag>
          {item.location && <Tag style={{ marginInlineEnd: 0 }}>{item.location}</Tag>}
          {item.next_action_at && (
            <Tag
              color={overdue ? 'red' : 'default'}
              icon={<ClockCircleOutlined />}
              style={{ marginInlineEnd: 0 }}
            >
              {overdue ? '逾期跟进 ' : '跟进 '}
              {dayjs(item.next_action_at).format('MM-DD')}
            </Tag>
          )}
        </Space>
        {item.notes && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--plan-muted)' }}>
            {item.notes.length > 60 ? `${item.notes.slice(0, 60)}…` : item.notes}
          </div>
        )}
        {nexts.length > 0 && (
          <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {nexts.slice(0, 2).map((s) => (
              <Button
                key={s}
                size="small"
                type={s === 'rejected' || s === 'withdrawn' ? 'text' : 'default'}
                danger={s === 'rejected'}
                onClick={() => advance(item, s)}
              >
                {STATUS_META[s].label}
              </Button>
            ))}
          </div>
        )}
      </Card>
    );
  };

  const columns = [
    {
      title: '公司 / 岗位',
      key: 'name',
      render: (_, r) => (
        <div>
          <Text strong>{r.company}</Text>
          <div style={{ fontSize: 12, color: 'var(--plan-muted)' }}>{r.position}</div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s) => <Tag color={STATUS_META[s]?.color}>{STATUS_META[s]?.label || s}</Tag>,
    },
    {
      title: '渠道',
      dataIndex: 'channel',
      width: 100,
      render: (c) => CHANNEL_LABEL[c] || c,
    },
    {
      title: '投递时间',
      dataIndex: 'applied_at',
      width: 120,
      render: (v) => (v ? dayjs(v).format('YYYY-MM-DD') : '—'),
    },
    {
      title: '下次跟进',
      dataIndex: 'next_action_at',
      width: 130,
      render: (v, r) =>
        v ? (
          <span style={{ color: isOverdue(r) ? '#cf1322' : undefined }}>
            {dayjs(v).format('MM-DD')}
            {isOverdue(r) ? '（逾期）' : ''}
          </span>
        ) : (
          '—'
        ),
    },
    {
      title: '操作',
      key: 'ops',
      width: 130,
      render: (_, r) => (
        <Space size={4}>
          <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm title="删除这条记录？" okText="删除" cancelText="取消" onConfirm={() => remove(r)}>
            <Button size="small" danger type="text">删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const hasData = items.length > 0;

  return (
    <div>
      {contextHolder}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Title level={2} style={{ margin: 0 }}>投递看板</Title>
          <Text type="secondary">记下每一次投递，就不会忘了哪家该跟进。</Text>
        </div>
        <Space wrap>
          <Segmented
            value={view}
            onChange={setView}
            options={[{ label: '看板', value: 'board' }, { label: '列表', value: 'list' }]}
          />
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>记一次投递</Button>
        </Space>
      </div>

      {stats && stats.total > 0 && (
        <Row gutter={16} style={{ marginTop: 20 }}>
          <Col xs={12} md={6}>
            <Card><Statistic title="已投递" value={stats.submitted} suffix={`/ ${stats.total}`} /></Card>
          </Col>
          <Col xs={12} md={6}>
            <Card><Statistic title="进入面试" value={stats.interview_count} /></Card>
          </Col>
          <Col xs={12} md={6}>
            <Card>
              <Statistic
                title="面试转化率"
                value={stats.interview_rate === null ? 0 : Math.round(stats.interview_rate * 100)}
                suffix="%"
              />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card>
              <Statistic
                title="待跟进"
                value={stats.due_followups}
                valueStyle={stats.due_followups > 0 ? { color: '#cf1322' } : undefined}
              />
            </Card>
          </Col>
        </Row>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : !hasData ? (
        <Card style={{ marginTop: 20 }}>
          <EmptyGuide
            icon={<ArrowRightOutlined />}
            title="还没有投递记录"
            description="把正在投或准备投的岗位记下来，之后每次状态变化点一下就能更新。投满十家之后，这里会告诉你哪个渠道最有效。"
            action={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>记下第一条投递</Button>}
          />
        </Card>
      ) : view === 'list' ? (
        <Card style={{ marginTop: 20 }}>
          <Table
            rowKey="id"
            size="middle"
            dataSource={items}
            columns={columns}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
          />
        </Card>
      ) : (
        <div style={{ marginTop: 20, overflowX: 'auto', paddingBottom: 8 }}>
          <div style={{ display: 'flex', gap: 12, minWidth: 7 * 200 }}>
            {Object.keys(STATUS_META).map((s) => (
              <div key={s} style={{ flex: '0 0 196px', minWidth: 196 }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 10px',
                    marginBottom: 8,
                    borderRadius: 10,
                    background: 'rgba(216, 233, 255, 0.6)',
                  }}
                >
                  <Tag color={STATUS_META[s].color} style={{ marginInlineEnd: 0 }}>
                    {STATUS_META[s].label}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>{grouped[s]?.length || 0}</Text>
                </div>
                <div style={{ minHeight: 80 }}>
                  {(grouped[s] || []).length === 0 ? (
                    <div
                      style={{
                        padding: '18px 8px',
                        textAlign: 'center',
                        fontSize: 12,
                        color: 'var(--plan-muted)',
                        border: '1px dashed var(--plan-line)',
                        borderRadius: 10,
                      }}
                    >
                      暂无
                    </div>
                  ) : (
                    grouped[s].map(renderCard)
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <Modal
        title={editing?.mode === 'create' ? '记一次投递' : '编辑投递记录'}
        open={Boolean(editing)}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        forceRender
        width={560}
      >
        <Form form={form} layout="vertical" onFinish={submit}>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="company" label="公司" rules={[{ required: true, message: '请填写公司名' }]}>
                <Input placeholder="如：字节跳动" maxLength={80} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="position" label="岗位" rules={[{ required: true, message: '请填写岗位' }]}>
                <Input placeholder="如：产品实习生" maxLength={120} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="channel" label="投递渠道">
                <Select
                  options={Object.entries(CHANNEL_LABEL).map(([value, label]) => ({ value, label }))}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="status" label="当前状态">
                <Select options={statusOptions} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="location" label="城市">
                <Input placeholder="如：上海" maxLength={60} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="salary_range" label="薪资">
                <Input placeholder="如：150-200/天" maxLength={40} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="next_action_at" label="下次跟进时间">
            <DatePicker style={{ width: '100%' }} showTime={{ format: 'HH:mm' }} format="YYYY-MM-DD HH:mm" />
          </Form.Item>
          <Form.Item name="jd_url" label="JD 链接">
            <Input placeholder="https://" maxLength={500} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={3} maxLength={4000} showCount placeholder="面试官问了什么、投递后多久回复…" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default Applications;
