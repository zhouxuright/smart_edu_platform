import React from 'react';

/**
 * 空状态引导。
 *
 * 空页面不该只写「暂无数据」——那等于告诉用户「这里什么都没有，你走吧」。
 * 统一成「说明这里是什么 + 下一步点哪里」，减少新用户卡在空白页。
 */
const EmptyGuide = ({ icon, title, description, action, compact = false }) => (
  <div className="empty-guide" style={compact ? { padding: '24px 16px' } : undefined}>
    {icon && (
      <div
        aria-hidden="true"
        style={{
          display: 'grid',
          placeItems: 'center',
          width: 46,
          height: 46,
          borderRadius: 15,
          background: 'rgba(216, 233, 255, 0.85)',
          color: 'var(--plan-accent)',
          fontSize: 21,
        }}
      >
        {icon}
      </div>
    )}
    <p className="empty-guide-title">{title}</p>
    {description && <p className="empty-guide-desc">{description}</p>}
    {action && <div style={{ marginTop: 4 }}>{action}</div>}
  </div>
);

export default EmptyGuide;
