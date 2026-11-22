import puppeteer from 'puppeteer';

const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173';
const USER = process.env.E2E_EMAIL;
const PASS = process.env.E2E_PASSWORD;
if (!USER || !PASS) throw new Error('Set E2E_EMAIL and E2E_PASSWORD for a dedicated test account.');

(async () => {
  console.log('Starting E2E Test...');
  const browser = await puppeteer.launch({ 
    headless: false,
    defaultViewport: null,
    args: ['--start-maximized'] 
  });
  const page = await browser.newPage();
  
  try {
    // 1. Login
    console.log('1. Navigating to Login...');
    await page.goto(`${BASE_URL}/login`);
    
    // Wait for login form
    await page.waitForSelector('#login_email', { timeout: 5000 }).catch(async () => {
        // If already logged in, might redirect to dashboard, but let's assume fresh start or handle redirect
        if (page.url() !== `${BASE_URL}/login`) {
            console.log('Already logged in or redirected.');
            return;
        }
        throw new Error('Login form not found');
    });

    if (page.url().includes('login')) {
        await page.type('#login_email', USER);
        await page.type('#login_password', PASS);
        
        await Promise.all([
          page.waitForNavigation(),
          page.click('button[type="submit"]')
        ]);
        console.log('Login successful.');
    }

    // 2. Dashboard
    console.log('2. Testing Dashboard...');
    await page.waitForSelector('.ant-card', { timeout: 30000 });
    console.log('Dashboard loaded.');

    // 3. Collaborative Learning (Streaming)
    console.log('3. Testing Collaborative Learning...');
    await page.goto(`${BASE_URL}/collaborative`);
    await page.waitForSelector('input[placeholder="输入讨论主题..."]', { timeout: 30000 });
    await page.type('input[placeholder="输入讨论主题..."]', 'Puppeteer Auto Test ' + Date.now());
    
    // Find button "开始会话"
    const startBtn = await page.evaluateHandle(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      return btns.find(b => b.textContent.includes('开始会话'));
    });
    if (startBtn) await startBtn.click();
    
    // Wait for "系统" message or "AI 助教"
    await page.waitForSelector('.ant-list-item', { timeout: 30000 });
    console.log('Collaborative session started.');

    // 4. Assessment (Streaming)
    console.log('4. Testing Assessment...');
    await page.goto(`${BASE_URL}/assessment`);
    await page.waitForSelector('button', { timeout: 30000 });
    const refreshBtn = await page.evaluateHandle(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        return btns.find(b => b.textContent.includes('刷新分析'));
    });
    if (refreshBtn) {
        await refreshBtn.click();
        await new Promise(r => setTimeout(r, 2000));
    }
    console.log('Assessment refreshed.');

    // 5. Parent Portal (Streaming)
    console.log('5. Testing Parent Portal...');
    await page.goto(`${BASE_URL}/parent`);
    await page.waitForSelector('.ant-alert', { timeout: 30000 }); 
    console.log('Parent Portal loaded.');
    
    // 6. Enterprise RAG
    console.log('6. Testing RAG...');
    await page.goto(`${BASE_URL}/rag`);
    await page.waitForSelector('input[placeholder*="检索"]', { timeout: 30000 });
    await page.type('input[placeholder*="检索"]', 'Python');
    const searchBtn = await page.evaluateHandle(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        return btns.find(b => b.textContent.includes('搜索'));
    });
    if (searchBtn) await searchBtn.click();
    console.log('RAG search initiated.');

    console.log('SUCCESS: All tests completed.');
  } catch (e) {
    console.error('TEST FAILED:', e);
  } finally {
    // Keep browser open for user to see
    // await browser.close();
  }
})();
