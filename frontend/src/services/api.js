import axios from 'axios';
/**
 * @typedef {Object} MentorMessage
 * @property {string} role 角色
 * @property {string} content 内容
 */
/**
 * @typedef {Object} HistoryItem
 * @property {string} role 角色
 * @property {string} content 内容
 */

export const API_URL = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '');

const api = axios.create({
    baseURL: API_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

// 在请求拦截器中附加认证令牌
api.interceptors.request.use(
    (config) => {
        const token = localStorage.getItem('token');
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// 统一处理非 2xx 的响应
api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (axios.isCancel && axios.isCancel(error)) {
            return Promise.reject(error);
        }
        const name = error?.name || '';
        const msg = error?.message || '';
        if (name === 'CanceledError' || String(msg).includes('canceled')) {
            return Promise.reject(error);
        }
        const status = error?.response?.status;
        const requestPath = error?.config?.url;
        if (status === 401 && requestPath !== '/login' && requestPath !== '/register' && window.location.pathname !== '/login') {
            authService.logout();
            window.location.replace('/login');
        }
        return Promise.reject(error);
    }
);

export const authService = {
    login: async (email, password) => {
        const response = await api.post('/login', { email, password });
        if (response.data.access_token) {
            localStorage.setItem('token', response.data.access_token);
            localStorage.setItem('role', response.data.role);
            localStorage.setItem('user_id', response.data.user_id);
        }
        return response.data;
    },
    // role 只允许 student / parent，后端会拒绝 admin 这类机构角色
    register: async (email, password, full_name, role = 'student') => {
        return await api.post('/register', { email, password, full_name, role });
    },
    logout: () => {
        localStorage.removeItem('token');
        localStorage.removeItem('role');
        localStorage.removeItem('user_id');
    }
};

export const agentService = {
    /**
     * @param {string} message
     * @param {HistoryItem[]} history
     */
    mentorChat: async (message, history, config) => {
        return await api.post('/agent/mentor', { message, history }, config);
    },
    /**
     * @param {string} subject
     * @param {string} topic
     */
    getKnowledgeGraph: async (subject, topic, config) => {
        return await api.post('/agent/knowledge', { subject, topic }, config);
    },
    /**
     * @param {number|string} student_id
     */
    getAssessment: async (student_id, config) => {
        return await api.post('/agent/assessment', { student_id }, config);
    },
    /**
     * @param {string} topic
     * @param {{user:string, content:string}[]} history
     */
    collaborate: async (topic, history, config) => {
        return await api.post('/agent/collaborative', { topic, history }, config);
    },
    /**
     * @param {number|string} student_id
     */
    getParentReport: async (student_id, config) => {
        return await api.post('/agent/parent', { student_id }, config);
    },
}
// 移除多余的右花括号

export const dashboardService = {
    /**
     * @param {{signal?: AbortSignal, studentId?: number}} options
     *   studentId 只在家长看孩子时传；学生传别人的 id 会被后端 scope 校验拦掉。
     */
    getSummary: async ({ signal, studentId } = {}) => {
        return await api.get('/dashboard/summary', {
            signal,
            ...(studentId ? { params: { student_id: studentId } } : {}),
        });
    },
    /**
     * @param {{subject:string, topic:string, activity_type:string, score?:number}} payload
     */
    createRecord: async (payload) => {
        return await api.post('/learning_records', payload);
    }
};

export const planService = {
    current: async () => {
        return await api.get('/internship-plans/current');
    },
    generate: async (payload) => {
        return await api.post('/internship-plans/generate', payload, { timeout: 40000 });
    },
    updateTask: async (taskId, changes) => {
        return await api.patch(`/internship-plans/tasks/${taskId}`, changes);
    },
    updateReminder: async (enabled, hour) => {
        return await api.put('/internship-plans/reminder', { enabled, hour });
    },
    recordEvent: async (event, planId) => {
        return await api.post('/internship-plans/events', { event, plan_id: planId ?? null });
    }
};

export const mistakeService = {
    create: async (data) => {
        return await api.post('/mistakes/', data);
    },
    analyze: async (mistakeId) => {
        return await api.post(`/mistakes/${mistakeId}/analyze`);
    },
    getAll: async () => {
        return await api.get('/mistakes/');
    },
    getOne: async (id) => {
        return await api.get(`/mistakes/${id}`);
    },
    ocr: async (formData) => {
        return await api.post('/mistake/ocr', formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });
    },
    batchUpload: async (formData) => {
        return await api.post('/mistakes/batch_upload', formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });
    },
    batchImageUpload: async (formData) => {
        return await api.post('/mistakes/batch_image_upload', formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });
    }
};

export const nlpService = {
    extractKnowledge: async (text, subject) => {
        return await api.post('/knowledge/extract', { text, subject });
    }
};

export const quotaService = {
    status: async () => {
        return await api.get('/llm/quota');
    }
};

export const notificationService = {
    unreadCount: async () => {
        return await api.get('/notifications/unread-count');
    },
    list: async (params) => {
        return await api.get('/notifications', { params });
    },
    markRead: async (id) => {
        return await api.post(`/notifications/${id}/read`);
    },
    readAll: async () => {
        return await api.post('/notifications/read-all');
    }
};

export const applicationService = {
    list: async (params) => {
        return await api.get('/applications', { params });
    },
    create: async (payload) => {
        return await api.post('/applications', payload);
    },
    update: async (id, changes) => {
        return await api.patch(`/applications/${id}`, changes);
    },
    remove: async (id) => {
        return await api.delete(`/applications/${id}`);
    },
    stats: async () => {
        return await api.get('/applications/stats');
    }
};

export const resumeService = {
    list: async () => {
        return await api.get('/resumes');
    },
    create: async (payload) => {
        return await api.post('/resumes', payload);
    },
    get: async (id) => {
        return await api.get(`/resumes/${id}`);
    },
    update: async (id, changes, { snapshot = false, note } = {}) => {
        return await api.patch(`/resumes/${id}`, { ...changes, ...(note ? { note } : {}) }, {
            params: snapshot ? { snapshot: true } : undefined,
        });
    },
    remove: async (id) => {
        return await api.delete(`/resumes/${id}`);
    },
    versions: async (id) => {
        return await api.get(`/resumes/${id}/versions`);
    },
    snapshot: async (id, note) => {
        return await api.post(`/resumes/${id}/versions`, { note });
    },
    restore: async (id, version) => {
        return await api.post(`/resumes/${id}/versions/${version}/restore`);
    },
    polish: async (id, payload) => {
        return await api.post(`/resumes/${id}/polish`, payload);
    },
    /** 导出 PDF：走 blob，避免被当 JSON 解析 */
    exportPdf: async (id) => {
        return await api.get(`/resumes/${id}/pdf`, { responseType: 'blob' });
    },
    /**
     * 附件简历：单传与批量共用这一个入口，files 里放 1 个就是单传。
     * 必须显式指定 multipart/form-data——axios 的默认头是 application/json，
     * 遇到 FormData 会把它序列化成 JSON，后端收到的就是一段坏数据。
     */
    uploadAttachments: async (id, files, onProgress) => {
        const form = new FormData();
        files.forEach((file) => form.append('files', file, file.name));
        return await api.post(`/resumes/${id}/attachments`, form, {
            headers: { 'Content-Type': 'multipart/form-data' },
            timeout: 180000,
            onUploadProgress: (event) => {
                if (onProgress && event.total) {
                    onProgress(Math.round((event.loaded / event.total) * 100));
                }
            },
        });
    },
    attachments: async (id) => {
        return await api.get(`/resumes/${id}/attachments`);
    },
    attachmentText: async (id, attachmentId) => {
        return await api.get(`/resumes/${id}/attachments/${attachmentId}/text`);
    },
    downloadAttachment: async (id, attachmentId) => {
        return await api.get(`/resumes/${id}/attachments/${attachmentId}/download`, {
            responseType: 'blob',
        });
    },
    removeAttachment: async (id, attachmentId) => {
        return await api.delete(`/resumes/${id}/attachments/${attachmentId}`);
    },
    /** 解析附件：apply=false 只返回预览，true 才覆盖当前结构化简历 */
    importAttachment: async (id, attachmentId, apply = false) => {
        return await api.post(
            `/resumes/${id}/attachments/${attachmentId}/import`,
            null,
            { params: apply ? { apply: true } : undefined, timeout: 120000 },
        );
    }
};

/**
 * 页面级 Agent（与上面的 agentService 不是一回事：那个是各业务智能体的聚合入口）。
 *
 * 一次对话只做一次规划：服务端先按页面跑读工具，再让模型决定要不要动手，
 * 所以这里没有「工具调用回合」的概念，前端只负责把 reply 和 proposal 分开渲染。
 * 提案必须经过 confirm 才会落库——这就是「用户点头之前不改数据」的那道闸。
 */
export const pageAgentService = {
    chat: async ({ page, pageId, message, history, sessionId }) => {
        return await api.post('/agent/chat', {
            page,
            page_id: pageId ?? null,
            message,
            // 老前端字段保留一轮：服务端只在「新会话且没有历史」时才用它
            history: (history || []).slice(-6),
            session_id: sessionId || null,
        }, { timeout: 120000 });
    },
    history: async (sessionId) => {
        return await api.get('/agent/chat/history', { params: { session_id: sessionId } });
    },
    confirm: async (proposalId, accept) => {
        return await api.post('/agent/chat/confirm', { proposal_id: proposalId, accept });
    },
    tools: async (page) => {
        return await api.get('/agent/chat/tools', { params: { page } });
    },
};

export const workflowService = {
    flows: async () => {
        return await api.get('/agent/flows');
    },
    // 默认一次推进最多 6 步：JD 匹配 4 步，一次请求走完
    start: async (kind, input) => {
        return await api.post('/agent/runs', { kind, input, max_steps: 6 }, { timeout: 180000 });
    },
    get: async (id) => {
        return await api.get(`/agent/runs/${id}`);
    },
    advance: async (id) => {
        return await api.post(`/agent/runs/${id}/advance`, {}, { timeout: 120000 });
    },
    // payload 只在多轮流程（模拟面试）用：用户这一轮的回答随确认一起提交。
    // 单步确认（JD 匹配、改简历提案）不传。
    resume: async (id, accept, payload) => {
        return await api.post(
            `/agent/runs/${id}/resume`,
            { accept, ...(payload ? { payload } : {}) },
            { timeout: 180000 },
        );
    },
    list: async (kind) => {
        return await api.get('/agent/runs', { params: kind ? { kind } : undefined });
    },
};

/**
 * 家长-学生绑定。
 *
 * 授权方是学生：邀请码由学生生成，家长拿码绑定。这样「知道邮箱」不等于
 * 「能看学情」，也让学生随时能在「谁在看我的数据」里撤销。
 */
export const parentLinkService = {
    // 学生侧
    createInvite: async () => {
        return await api.post('/parent/invite', {});
    },
    viewers: async () => {
        return await api.get('/parent/viewers');
    },
    revoke: async (parentId) => {
        return await api.delete(`/parent/viewers/${parentId}`);
    },
    // 家长侧
    children: async () => {
        return await api.get('/parent/children');
    },
    bind: async (code) => {
        return await api.post('/parent/link', { code });
    },
    unbind: async (studentId) => {
        return await api.delete(`/parent/children/${studentId}`);
    },
};

export default api;
