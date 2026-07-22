// 断线恢复 e2e:发一条真消息 → 1.5s 后刷新页面(模拟切走/关窗)→ 不操作,等回答自己出现。
import { chromium } from 'playwright-core'

const out = process.argv[2] ?? '/tmp/epictrace-shots'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 820 } })
const errors = []
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console.error: ${m.text()}`) })

await page.goto('http://localhost:5199', { waitUntil: 'networkidle' })
await page.getByRole('button', { name: '项目与对话' }).click()
await page.waitForTimeout(400)
// 新建对话 → 发一条轻量真消息(不触发工具,几秒就答完)
await page.getByRole('button', { name: '新建对话' }).first().click()
await page.waitForTimeout(500)
await page.locator('textarea[aria-label="对话输入"]').fill('只回复「收到」两个字,不要用任何工具')
await page.keyboard.press('Enter')
await page.waitForTimeout(1500) // 确认 turn 已在后端开跑
await page.screenshot({ path: `${out}/recover-1-before-reload.png` })

// 模拟切走/关窗:刷新页面,SSE 断,组件重挂载
await page.reload({ waitUntil: 'networkidle' })
await page.screenshot({ path: `${out}/recover-2-after-reload.png` })

// 回到那个仍在运行的会话(树里的琥珀点说明后端还在跑)
await page.getByText('会话 #26').first().click()
await page.waitForTimeout(1000)

// 不做任何操作:恢复轮询应在跑完后把回答补拉回来
let found = false
for (let i = 0; i < 45; i++) {
  await page.waitForTimeout(2000)
  const body = await page.locator('section, main, body').first().innerText().catch(() => '')
  if (body.includes('收到') && !body.includes('思考中')) { found = true; break }
}
await page.screenshot({ path: `${out}/recover-3-final.png` })
console.log(JSON.stringify({ recovered: found, errors }, null, 2))
await browser.close()
