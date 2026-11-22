export const CAREER_MENTOR_PATH = '/mentor?focus=career';
export const CAREER_QUESTION_LIMIT = 800;
const DRAFT_KEY = 'career-mentor-draft-v1';
const MAX_AGE_MS = 30 * 60 * 1000;
let inMemoryDraft;

const returnPaths = new Set([
  '/dashboard', '/mentor', CAREER_MENTOR_PATH, '/knowledge', '/rag',
  '/assessment', '/collaborative', '/parent', '/mistake-book', '/knowledge-extraction',
  '/plan/internship',
]);

export function saveMentorDraft(question) {
  const draft = { question: question.trim().slice(0, CAREER_QUESTION_LIMIT), savedAt: Date.now() };
  inMemoryDraft = draft;
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // Restrictive browser settings still allow in-app navigation with the draft.
  }
}

export function clearMentorDraft() {
  inMemoryDraft = undefined;
  try {
    sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // There may be no writable session storage.
  }
}

export function readMentorDraft() {
  let draft = inMemoryDraft;
  if (!draft) {
    try {
      draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || 'null');
    } catch {
      return '';
    }
  }
  if (!draft) return '';
  if (typeof draft.question !== 'string' || !Number.isFinite(draft.savedAt) || Date.now() - draft.savedAt >= MAX_AGE_MS) {
    clearMentorDraft();
    return '';
  }
  return draft.question.slice(0, CAREER_QUESTION_LIMIT);
}

export function resolveAuthReturnPath(requestedPath) {
  if (returnPaths.has(requestedPath)) return requestedPath;
  return readMentorDraft() ? CAREER_MENTOR_PATH : '/dashboard';
}
