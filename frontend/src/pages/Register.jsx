import React, { useState } from 'react';
import { Card, Form, Input, Button, message } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { authService } from '../services/api';
import { resolveAuthReturnPath } from '../services/mentorDraft';

const Register = () => {
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const returnTo = resolveAuthReturnPath(location.state?.returnTo);
  const [messageApi, contextHolder] = message.useMessage();

  const onFinish = async (values) => {
    setLoading(true);
    try {
      await authService.register(values.email, values.password, values.full_name);
      // 注册后直接登录，避免多一次跳转把试用的人劝退
      try {
        await authService.login(values.email, values.password);
        messageApi.success('注册成功，已为你登录。');
        navigate(returnTo, { replace: true });
        return;
      } catch {
        messageApi.success('注册成功，请登录。');
        navigate('/login', { replace: true, state: { returnTo } });
      }
    } catch (error) {
      messageApi.error(error?.response?.data?.detail === '邮箱已被注册' ? '该邮箱已注册，请直接登录。' : '注册失败，请稍后重试。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
      {contextHolder}
      <Card title="注册新账号" className="glass-card" style={{ width: 'min(400px, calc(100% - 32px))', border: 'none' }}>
        <Form
          name="register"
          onFinish={onFinish}
          layout="vertical"
        >
          <Form.Item
            label="姓名"
            name="full_name"
            rules={[{ required: true, message: '请输入您的姓名！' }]}
          >
            <Input />
          </Form.Item>

          <Form.Item
            label="邮箱"
            name="email"
            rules={[
              { required: true, message: '请输入您的邮箱！' },
              { type: 'email', message: '请输入有效的邮箱！' }
            ]}
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
              注册
            </Button>
            <div style={{ marginTop: 10, textAlign: 'center' }}>
               已有账号？ <Link to="/login" state={{ returnTo }}>去登录</Link> · <Link to="/">返回首页</Link>
            </div>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default Register;
