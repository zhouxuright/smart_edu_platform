import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Drawer, Empty, List, Space, Tag, Typography } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { notificationService } from '../services/api';

const { Text } = Typography;

const POLL_MS = 60000;

/**
 * 顶栏通知铃铛。
 *
 * 这是「第二天提醒」真正的落点：后端定时扫描计划提醒并写入站内信，
 * 用户下次进站就能看到，不再依赖他自己想起打开计划页。
 */
const NotificationBell = () => {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const refreshCount = useCallback(async () => {
    try {
      const res = await notificationService.unreadCount();
      setUnread(res.data.unread || 0);
    } catch {
      /* 未读数拉取失败不打扰用户 */
    }
  }, []);

  useEffect(() => {
    refreshCount();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') refreshCount();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [refreshCount]);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await notificationService.list({ limit: 50 });
      setItems(res.data.items || []);
    } catch {
      /* 静默 */
    } finally {
      setLoading(false);
    }
  }, []);

  const openDrawer = () => {
    setOpen(true);
    loadList();
  };

  const handleClick = async (item) => {
    if (!item.read) {
      try {
        await notificationService.markRead(item.id);
        setItems((prev) => prev.map((it) => (it.id === item.id ? { ...it, read: true } : it)));
        refreshCount();
      } catch {
        /* 静默 */
      }
    }
    if (item.link) {
      setOpen(false);
      navigate(item.link);
    }
  };

  const readAll = async () => {
    try {
      await notificationService.readAll();
      setItems((prev) => prev.map((it) => ({ ...it, read: true })));
      setUnread(0);
    } catch {
      /* 静默 */
    }
  };

  return (
    <>
      <Badge count={unread} size="small" offset={[-2, 2]}>
        <Button
          type="text"
          icon={<BellOutlined />}
          onClick={openDrawer}
          aria-label={unread ? `通知，${unread} 条未读` : '通知'}
        />
      </Badge>

      <Drawer
        title="通知"
        placement="right"
        width={380}
        open={open}
        onClose={() => setOpen(false)}
        extra={
          items.some((it) => !it.read) ? (
            <Button size="small" type="link" onClick={readAll}>全部已读</Button>
          ) : null
        }
      >
        {items.length === 0 && !loading ? (
          <Empty description="还没有通知" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <List
            loading={loading}
            dataSource={items}
            renderItem={(item) => (
              <List.Item
                style={{ cursor: item.link ? 'pointer' : 'default', alignItems: 'flex-start' }}
                onClick={() => handleClick(item)}
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space size={6} wrap>
                    {!item.read && <Tag color="blue" style={{ marginInlineEnd: 0 }}>未读</Tag>}
                    <Text strong={!item.read} style={{ fontSize: 14 }}>{item.title}</Text>
                  </Space>
                  {item.body && (
                    <Text
                      type="secondary"
                      style={{ fontSize: 13, whiteSpace: 'pre-wrap', display: 'block' }}
                    >
                      {item.body}
                    </Text>
                  )}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.created_at ? dayjs(item.created_at).format('MM-DD HH:mm') : ''}
                  </Text>
                </Space>
              </List.Item>
            )}
          />
        )}
      </Drawer>
    </>
  );
};

export default NotificationBell;
