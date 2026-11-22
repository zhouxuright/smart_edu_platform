
import React, { useState, useEffect } from 'react';
import { List, Card, Button, Modal, Form, Input, Select, Tag, Typography, Spin, message, Row, Col, Upload, Space } from 'antd';
import { PlusOutlined, RobotOutlined, CheckCircleOutlined, UploadOutlined, LoadingOutlined, BookOutlined } from '@ant-design/icons';
import { mistakeService } from '../services/api';
import EmptyGuide from '../components/EmptyGuide';
import { track } from '../services/telemetry';

const { TextArea } = Input;
const { Title, Text, Paragraph } = Typography;

const MistakeBook = () => {
    const [mistakes, setMistakes] = useState([]);
    const [loading, setLoading] = useState(false);
    const [isModalVisible, setIsModalVisible] = useState(false);
    const [selectedMistake, setSelectedMistake] = useState(null);
    const [detailVisible, setDetailVisible] = useState(false);
    const [analyzing, setAnalyzing] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [isBatchModalVisible, setIsBatchModalVisible] = useState(false);
    const [batchUploading, setBatchUploading] = useState(false);
    const [isBatchImageModalVisible, setIsBatchImageModalVisible] = useState(false);
    const [batchImageUploading, setBatchImageUploading] = useState(false);
    const [batchFileList, setBatchFileList] = useState([]);
    const [form] = Form.useForm();

    const fetchMistakes = async () => {
        setLoading(true);
        try {
            const data = await mistakeService.getAll();
            setMistakes(data.data);
        } catch (error) {
            message.error('获取错题列表失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchMistakes();
    }, []);

    const handleCreate = async (values) => {
        try {
            await mistakeService.create(values);
            track('mistake_create', { subject: values.subject || '' });
            message.success('错题添加成功');
            setIsModalVisible(false);
            form.resetFields();
            fetchMistakes();
        } catch (error) {
            message.error('添加失败');
        }
    };

    const handleUpload = async (file) => {
        setUploading(true);
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await mistakeService.ocr(formData);
            if (res.data.text) {
                const currentContent = form.getFieldValue('question_content') || '';
                const newContent = currentContent ? currentContent + '\n' + res.data.text : res.data.text;
                form.setFieldsValue({ question_content: newContent });
                message.success('图片识别成功');
            }
        } catch (error) {
            message.error('识别失败: ' + (error.response?.data?.detail || error.message));
        } finally {
            setUploading(false);
        }
        return false; // Prevent default upload
    };

    const handleBatchUpload = async (file) => {
        setBatchUploading(true);
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await mistakeService.batchUpload(formData);
            message.success(`Successfully imported ${res.data.count} mistakes`);
            setIsBatchModalVisible(false);
            fetchMistakes();
        } catch (error) {
            message.error('Import failed: ' + (error.response?.data?.detail || error.message));
        } finally {
            setBatchUploading(false);
        }
        return false;
    };

    const handleBatchImageUpload = async () => {
        if (batchFileList.length === 0) {
            message.warning('Please select images first');
            return;
        }
        setBatchImageUploading(true);
        const formData = new FormData();
        batchFileList.forEach(file => {
            formData.append('files', file);
        });

        try {
            const res = await mistakeService.batchImageUpload(formData);
            message.success(res.data.message || 'Batch image upload successful');
            setIsBatchImageModalVisible(false);
            setBatchFileList([]);
            fetchMistakes();
        } catch (error) {
            message.error('Batch upload failed: ' + (error.response?.data?.detail || error.message));
        } finally {
            setBatchImageUploading(false);
        }
    };

    const handleAnalyze = async (mistake) => {
        setAnalyzing(true);
        try {
            const res = await mistakeService.analyze(mistake.id);
            const updatedMistake = res.data;
            
            // Update local state
            setMistakes(prev => prev.map(m => m.id === updatedMistake.id ? updatedMistake : m));
            setSelectedMistake(updatedMistake);
            message.success('分析完成');
        } catch (error) {
            message.error('分析失败');
        } finally {
            setAnalyzing(false);
        }
    };

    const openDetail = (mistake) => {
        setSelectedMistake(mistake);
        setDetailVisible(true);
    };

    const renderAnalysis = (analysis) => {
        if (!analysis) return <Text type="secondary">暂无分析报告，请点击"智能分析"按钮获取。</Text>;
        
        // Handle case where analysis is a string (JSON string) or object
        let data = analysis;
        if (typeof analysis === 'string') {
            try {
                data = JSON.parse(analysis);
            } catch (e) {
                return <Text>{analysis}</Text>;
            }
        }

        return (
            <div style={{ marginTop: 16 }}>
                <Title level={4}>智能分析报告</Title>
                
                <Title level={5}>知识点</Title>
                <div>
                    {data.knowledge_points?.map((kp, i) => <Tag color="blue" key={i}>{kp}</Tag>)}
                </div>

                <Title level={5} style={{ marginTop: 12 }}>难度等级: <Tag color="orange">{data.difficulty}/5</Tag></Title>
                
                <Title level={5}>错误原因</Title>
                <Paragraph>{data.error_cause_analysis}</Paragraph>

                <Title level={5}>正确解法</Title>
                <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{data.correct_solution}</Paragraph>

                <Title level={5}>学习建议</Title>
                <Paragraph>{data.learning_suggestion}</Paragraph>

                {data.similar_problem && (
                    <Card size="small" title="举一反三" style={{ marginTop: 12, background: 'rgba(216, 233, 255, 0.4)' }}>
                        <Text strong>题目：</Text>
                        <Paragraph>{data.similar_problem.question}</Paragraph>
                        <Text strong>答案：</Text>
                        <Paragraph>{data.similar_problem.answer}</Paragraph>
                    </Card>
                )}
            </div>
        );
    };

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <Title level={2} style={{ color: 'var(--plan-ink)', margin: 0 }}>智能错题本</Title>
                <div>
                    <Button icon={<UploadOutlined />} onClick={() => setIsBatchImageModalVisible(true)} style={{ marginRight: 8 }}>
                        批量图片
                    </Button>
                    <Button icon={<UploadOutlined />} onClick={() => setIsBatchModalVisible(true)} style={{ marginRight: 8 }}>
                        批量导入
                    </Button>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsModalVisible(true)}>
                        录入错题
                    </Button>
                </div>
            </div>

            <List
                grid={{ gutter: 16, column: 3 }}
                loading={loading}
                dataSource={mistakes}
                locale={{
                    emptyText: (
                        <EmptyGuide
                            icon={<BookOutlined />}
                            title="还没有录入错题"
                            description="把做错的题拍下来或粘贴文字，系统会自动分析错因、归纳知识点，并给出举一反三的同类题。"
                            action={
                                <Space wrap>
                                    <Button type="primary" onClick={() => setIsModalVisible(true)}>
                                        录入第一道错题
                                    </Button>
                                </Space>
                            }
                        />
                    ),
                }}
                renderItem={item => (
                    <List.Item>
                        <Card 
                            title={item.subject} 
                            extra={<Tag color={item.is_resolved ? "green" : "red"}>{item.is_resolved ? "已掌握" : "未掌握"}</Tag>}
                            hoverable
                            onClick={() => openDetail(item)}
                            actions={[
                                <Button type="link" onClick={(e) => { e.stopPropagation(); openDetail(item); }}>查看详情</Button>
                            ]}
                            style={{ background: 'rgba(216, 233, 255, 0.55)', border: '1px solid var(--plan-line)' }}
                            styles={{ header: { color: 'var(--plan-ink)', borderBottom: '1px solid var(--plan-line)' }, body: { color: 'var(--plan-muted)' } }}
                        >
                            <Paragraph ellipsis={{ rows: 3 }}>{item.question_content}</Paragraph>
                            <Text type="secondary" style={{ color: 'var(--plan-muted)' }}>{new Date(item.created_at).toLocaleDateString()}</Text>
                        </Card>
                    </List.Item>
                )}
            />

            <Modal
                title="录入错题"
                open={isModalVisible}
                onCancel={() => setIsModalVisible(false)}
                footer={null}
            >
                <Form form={form} onFinish={handleCreate} layout="vertical">
                    <Form.Item name="subject" label="学科" rules={[{ required: true }]}>
                        <Select>
                            <Select.Option value="数学">数学</Select.Option>
                            <Select.Option value="物理">物理</Select.Option>
                            <Select.Option value="化学">化学</Select.Option>
                            <Select.Option value="英语">英语</Select.Option>
                            <Select.Option value="语文">语文</Select.Option>
                            <Select.Option value="历史">历史</Select.Option>
                            <Select.Option value="地理">地理</Select.Option>
                            <Select.Option value="生物">生物</Select.Option>
                            <Select.Option value="政治">政治</Select.Option>
                        </Select>
                    </Form.Item>
                    <Form.Item name="topic" label="主题/章节">
                        <Input placeholder="例如：三角函数" />
                    </Form.Item>
                    <Form.Item label="拍照/上传识别">
                         <Upload 
                            beforeUpload={handleUpload} 
                            showUploadList={false}
                            accept="image/*"
                        >
                            <Button icon={uploading ? <LoadingOutlined /> : <UploadOutlined />} loading={uploading}>
                                {uploading ? '识别中...' : '上传图片识别题目'}
                            </Button>
                        </Upload>
                    </Form.Item>
                    <Form.Item name="question_content" label="题目内容" rules={[{ required: true }]}>
                        <TextArea rows={4} placeholder="请输入题目文本..." />
                    </Form.Item>
                    <Form.Item name="image_url" label="图片链接 (可选)">
                        <Input placeholder="http://..." />
                    </Form.Item>
                    <Form.Item>
                        <Button type="primary" htmlType="submit" block>提交</Button>
                    </Form.Item>
                </Form>
            </Modal>

            <Modal
                title={selectedMistake?.subject + " - 错题详情"}
                open={detailVisible}
                onCancel={() => setDetailVisible(false)}
                width={800}
                footer={[
                    <Button key="close" onClick={() => setDetailVisible(false)}>关闭</Button>,
                    !selectedMistake?.analysis_result && (
                        <Button 
                            key="analyze" 
                            type="primary" 
                            icon={<RobotOutlined />} 
                            loading={analyzing}
                            onClick={() => handleAnalyze(selectedMistake)}
                        >
                            智能分析
                        </Button>
                    )
                ]}
            >
                {selectedMistake && (
                    <Row gutter={24}>
                        <Col span={12}>
                            <Title level={5}>题目</Title>
                            <Paragraph style={{ background: '#f5f5f5', padding: 12, borderRadius: 6 }}>
                                {selectedMistake.question_content}
                            </Paragraph>
                            {selectedMistake.image_url && (
                                <img src={selectedMistake.image_url} alt="题目图片" style={{ maxWidth: '100%', marginTop: 8 }} />
                            )}
                            <div style={{ marginTop: 16 }}>
                                <Text type="secondary">录入时间: {new Date(selectedMistake.created_at).toLocaleString()}</Text>
                            </div>
                        </Col>
                        <Col span={12} style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 24 }}>
                            {renderAnalysis(selectedMistake.analysis_result)}
                        </Col>
                    </Row>
                )}
            </Modal>

            <Modal
                title="批量导入错题"
                open={isBatchModalVisible}
                onCancel={() => setIsBatchModalVisible(false)}
                footer={null}
            >
                <div style={{ textAlign: 'center', padding: '20px 0' }}>
                    <p>请上传 .xlsx 或 .csv 文件</p>
                    <p style={{ fontSize: 12, color: '#888', marginBottom: 20 }}>
                        文件必须包含表头，支持的列名：<br/>
                        <b>科目</b> (必填), <b>题目/内容</b> (必填), <br/>
                        <b>主题/知识点</b>, <b>图片/URL</b>
                    </p>
                    <Upload.Dragger
                        accept=".xlsx,.csv"
                        beforeUpload={handleBatchUpload}
                        showUploadList={false}
                        disabled={batchUploading}
                    >
                        <p className="ant-upload-drag-icon">
                            {batchUploading ? <LoadingOutlined /> : <UploadOutlined />}
                        </p>
                        <p className="ant-upload-text">
                            {batchUploading ? '正在导入...' : '点击或拖拽文件到此区域上传'}
                        </p>
                    </Upload.Dragger>
                </div>
            </Modal>

            <Modal
                title="批量图片上传 (生成错题本)"
                open={isBatchImageModalVisible}
                onCancel={() => {
                    setIsBatchImageModalVisible(false);
                    setBatchFileList([]);
                }}
                footer={[
                    <Button key="cancel" onClick={() => setIsBatchImageModalVisible(false)}>
                        取消
                    </Button>,
                    <Button
                        key="submit"
                        type="primary"
                        loading={batchImageUploading}
                        onClick={handleBatchImageUpload}
                        disabled={batchFileList.length === 0}
                    >
                        开始上传
                    </Button>,
                ]}
            >
                <div style={{ padding: '20px 0' }}>
                    <Upload.Dragger
                        multiple={true}
                        fileList={batchFileList}
                        beforeUpload={(file) => {
                            setBatchFileList(prev => [...prev, file]);
                            return false;
                        }}
                        onRemove={(file) => {
                            setBatchFileList(prev => prev.filter(f => f.uid !== file.uid));
                        }}
                        accept="image/*"
                    >
                        <p className="ant-upload-drag-icon">
                            <UploadOutlined />
                        </p>
                        <p className="ant-upload-text">
                            点击或拖拽多张图片到此区域
                        </p>
                        <p className="ant-upload-hint">
                            已选择 {batchFileList.length} 张图片
                        </p>
                    </Upload.Dragger>
                </div>
            </Modal>
        </div>
    );
};

export default MistakeBook;
