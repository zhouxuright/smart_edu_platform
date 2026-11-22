import React, { useState } from 'react';
import { Card, Form, Input, Button, message } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { authService } from '../services/api';
import { CAREER_MENTOR_PATH, readMentorDraft, resolveAuthReturnPath } from '../services/mentorDraft';

const Login = () => {
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const returnTo = resolveAuthReturnPath(location.state?.returnTo);
  const hasDraft = returnTo === CAREER_MENTOR_PATH && Boolean(readMentorDraft());
  const [messageApi, contextHolder] = message.useMessage();

  const onFinish = async (values) => {
    setLoading(true);
    try {
      await authService.login(values.email, values.password);
      messageApi.success('登录成功');
      navigate(returnTo, { replace: true });
    } catch (error) {
      messageApi.error(error?.response?.status === 401 ? '邮箱或密码不正确，请重试。' : '暂时无法登录，请稍后重试。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
      {contextHolder}
      <Card title="登录启程 AI" className="glass-card" style={{ width: 'min(400px, calc(100% - 32px))', border: 'none' }}>
        {hasDraft && <p style={{ margin: '0 0 18px' }}>已保留你的问题，登录后可继续编辑并发送。</p>}
        <Form
          name="login"
          initialValues={{ remember: true }}
          onFinish={onFinish}
          layout="vertical"
        >
          <Form.Item
            label="邮箱"
            name="email"
            rules={[{ required: true, message: '请输入您的邮箱！' }]}
          >
            <Input />
          </Form.Item>

          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入您的密码！' }]}
          >
            <Input.Password />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              登录
            </Button>
            <div style={{ marginTop: 10, textAlign: 'center' }}>
               <Link to="/register" state={{ returnTo }}>创建学生账号</Link> · <Link to="/">返回首页</Link>
            </div>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default Login;
