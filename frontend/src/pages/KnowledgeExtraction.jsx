
import React, { useState } from 'react';
import { Card, Input, Button, Table, Tag, Typography, message, Row, Col, Space } from 'antd';
import { SearchOutlined, BookOutlined, ShareAltOutlined } from '@ant-design/icons';
import { nlpService } from '../services/api';

const { TextArea } = Input;
const { Title, Text, Paragraph } = Typography;

const KnowledgeExtraction = () => {
    const [text, setText] = useState('');
    const [subject, setSubject] = useState('');
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState(null);

    const handleExtract = async () => {
        if (!text.trim()) {
            message.warning('请输入需要分析的文本');
            return;
        }

        setLoading(true);
        try {
            const res = await nlpService.extractKnowledge(text, subject || undefined);
            setResult(res.data);
            message.success('抽取完成');
        } catch (error) {
            message.error('抽取失败，请重试');
        } finally {
            setLoading(false);
        }
    };

    const kpColumns = [
        {
            title: '知识点名称',
            dataIndex: 'name',
            key: 'name',
            render: text => <Text strong>{text}</Text>,
        },
        {
            title: '类别',
            dataIndex: 'category',
            key: 'category',
            render: text => <Tag color="blue">{text}</Tag>,
        },
        {
            title: '描述',
            dataIndex: 'description',
            key: 'description',
        },
    ];

    const linkColumns = [
        {
            title: '源知识点',
            dataIndex: 'source_point',
            key: 'source_point',
            render: text => <Tag color="geekblue">{text}</Tag>,
        },
        {
            title: '关联关系',
            dataIndex: 'relationship',
            key: 'relationship',
            render: text => <Text type="secondary">{text}</Text>,
        },
        {
            title: '目标学科',
            dataIndex: 'target_subject',
            key: 'target_subject',
            render: text => <Tag color="purple">{text}</Tag>,
        },
        {
            title: '目标概念',
            dataIndex: 'target_concept',
            key: 'target_concept',
            render: text => <Text strong>{text}</Text>,
        },
    ];

    return (
        <div>
            <Title level={2} style={{ color: 'var(--plan-ink)' }}>多学科知识抽取与关联</Title>
            <Paragraph style={{ color: 'var(--plan-muted)' }}>
                输入一段文本（如课文、题目、笔记），系统将自动抽取核心知识点，并分析其与其他学科的潜在关联。
            </Paragraph>

            <Row gutter={24}>
                <Col span={10}>
                    <Card 
                        title="文本输入" 
                        style={{ height: '100%', background: 'rgba(216, 233, 255, 0.55)', border: '1px solid var(--plan-line)' }} 
                        styles={{ header: { color: 'var(--plan-ink)' }, body: { color: 'var(--plan-ink)' } }}
                    >
                        <Space direction="vertical" style={{ width: '100%' }}>
                            <Input 
                                placeholder="预设学科 (可选，如：历史)" 
                                value={subject} 
                                onChange={e => setSubject(e.target.value)} 
                                style={{ marginBottom: 8 }}
                            />
                            <TextArea 
                                rows={12} 
                                placeholder="请输入文本内容..." 
                                value={text} 
                                onChange={e => setText(e.target.value)} 
                            />
                            <Button 
                                type="primary" 
                                icon={<SearchOutlined />} 
                                loading={loading} 
                                onClick={handleExtract} 
                                block
                                size="large"
                            >
                                开始抽取与分析
                            </Button>
                        </Space>
                    </Card>
                </Col>
                
                <Col span={14}>
                    <Card 
                        title="分析结果" 
                        style={{ minHeight: 500, background: 'rgba(216, 233, 255, 0.55)', border: '1px solid var(--plan-line)' }} 
                        styles={{
                            header: { color: 'var(--plan-ink)' },
                            body: { padding: 0 }
                        }}
                    >
                        {!result && !loading && (
                            <div style={{ padding: 40, textAlign: 'center', color: 'var(--plan-muted)' }}>
                                <BookOutlined style={{ fontSize: 48, marginBottom: 16 }} />
                                <p>请在左侧输入文本并点击分析按钮</p>
                            </div>
                        )}
                        
                        {loading && (
                            <div style={{ padding: 40, textAlign: 'center', color: 'var(--plan-muted)' }}>
                                <div style={{ marginBottom: 16 }}>正在调用NLP模型进行分析...</div>
                            </div>
                        )}

                        {result && (
                            <div style={{ padding: 16 }}>
                                <div style={{ marginBottom: 16 }}>
                                    <Text style={{ color: 'var(--plan-ink)' }}>主要学科: </Text>
                                    <Tag color="gold" style={{ fontSize: 16, padding: '4px 10px' }}>{result.main_subject}</Tag>
                                </div>

                                <Card type="inner" title="核心知识点" size="small" style={{ marginBottom: 16 }}>
                                    <Table 
                                        dataSource={result.knowledge_points} 
                                        columns={kpColumns} 
                                        pagination={false} 
                                        size="small"
                                        rowKey="name"
                                    />
                                </Card>

                                <Card type="inner" title={<span><ShareAltOutlined /> 跨学科关联</span>} size="small">
                                    <Table 
                                        dataSource={result.cross_disciplinary_links} 
                                        columns={linkColumns} 
                                        pagination={false} 
                                        size="small"
                                        rowKey={(record) => record.source_point + record.target_concept}
                                    />
                                </Card>
                            </div>
                        )}
                    </Card>
                </Col>
            </Row>
        </div>
    );
};

export default KnowledgeExtraction;
