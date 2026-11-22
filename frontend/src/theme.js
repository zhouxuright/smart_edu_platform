import { theme } from 'antd';

/**
 * 全局主题：浅蓝（与「第一份实习 7 天计划」页保持同一套色板）。
 *
 * 之前仪表盘等页面是深色玻璃拟态、计划页是浅蓝，两套皮肤并存导致跳转时观感割裂。
 * 这里把色板收敛到一处，index.css 的 CSS 变量与这里的 token 取值保持一致，
 * 后续调色只改这一份。
 */
export const palette = {
  bg: '#e8f2ff',
  bgDeep: '#d8e9ff',
  surface: '#f1f8ff',
  line: '#cbdcf2',
  lineSoft: '#dce8f8',
  ink: '#12294d',
  inkMuted: '#4c6285',
  accent: '#4a6cf7',
  accentDeep: '#3856cf',
  danger: '#8c2747',
  success: '#1f8a54',
};

export const appTheme = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: palette.accent,
    colorInfo: palette.accent,
    colorSuccess: palette.success,
    colorError: palette.danger,
    colorBgBase: palette.bg,
    colorBgLayout: 'transparent',
    colorBgContainer: 'rgba(255, 255, 255, 0.78)',
    colorBgElevated: '#ffffff',
    colorText: palette.ink,
    colorTextSecondary: palette.inkMuted,
    colorTextTertiary: palette.inkMuted,
    colorBorder: palette.line,
    colorBorderSecondary: palette.lineSoft,
    borderRadius: 12,
    fontSize: 14,
    fontFamily:
      '"Microsoft YaHei", "PingFang SC", system-ui, -apple-system, "Segoe UI", sans-serif',
  },
  components: {
    Layout: {
      bodyBg: 'transparent',
      headerBg: 'transparent',
      siderBg: 'transparent',
    },
    Menu: {
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemColor: palette.inkMuted,
      itemHoverBg: 'rgba(74, 108, 247, 0.08)',
      itemHoverColor: palette.ink,
      itemSelectedBg: 'rgba(74, 108, 247, 0.14)',
      itemSelectedColor: palette.accentDeep,
      itemBorderRadius: 10,
    },
    Card: {
      colorBgContainer: 'rgba(255, 255, 255, 0.78)',
    },
    Table: {
      headerBg: 'rgba(216, 233, 255, 0.7)',
      headerColor: palette.ink,
      rowHoverBg: 'rgba(216, 233, 255, 0.5)',
    },
    Input: {
      colorBgContainer: '#ffffff',
    },
    Select: {
      colorBgContainer: '#ffffff',
    },
    Modal: {
      contentBg: palette.surface,
      headerBg: palette.surface,
    },
    Tag: {
      defaultBg: 'rgba(216, 233, 255, 0.9)',
      defaultColor: palette.inkMuted,
    },
  },
};

export default appTheme;
