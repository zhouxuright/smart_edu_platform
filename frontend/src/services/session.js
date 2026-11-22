/**
 * 登录态读取。
 *
 * 后端只下发 access_token（JWT），role 与 user_id 另外存 localStorage。
 * 邮箱没有单独存，从 JWT 的 sub 里解出来——只用于顶栏展示，不参与鉴权判断。
 */

export function getRole() {
  return (localStorage.getItem('role') || 'student').toLowerCase();
}

export function getUserId() {
  return localStorage.getItem('user_id') || '';
}

export function getToken() {
  return localStorage.getItem('token') || '';
}

function decodeBase64Url(segment) {
  try {
    const padded = segment.replace(/-/g, '+').replace(/_/g, '/');
    const pad = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
    return decodeURIComponent(
      atob(padded + pad)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
  } catch {
    return '';
  }
}

export function getTokenClaims() {
  const token = getToken();
  const parts = token.split('.');
  if (parts.length < 2) return {};
  try {
    return JSON.parse(decodeBase64Url(parts[1])) || {};
  } catch {
    return {};
  }
}

export function getUserEmail() {
  return getTokenClaims().sub || '';
}

/** 顶栏展示用：优先邮箱前缀，没有就退回角色名 */
export function getDisplayName() {
  const email = getUserEmail();
  if (!email) return '';
  return email.split('@')[0];
}

export const ROLE_LABEL = {
  student: '学生',
  parent: '家长',
  admin: '管理员',
};

export function getRoleLabel() {
  return ROLE_LABEL[getRole()] || '用户';
}
