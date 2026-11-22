import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { trackPageView } from '../services/telemetry';

/**
 * 路由级页面浏览采集。
 *
 * 放在 Router 内部而不是 MainLayout 里，是因为「我的计划」等页面是独立整页、
 * 不经过布局，挂在布局上会漏掉这些路径。未登录时 track 内部会直接跳过。
 */
const PageViewTracker = () => {
  const { pathname } = useLocation();

  useEffect(() => {
    trackPageView(pathname);
  }, [pathname]);

  return null;
};

export default PageViewTracker;
