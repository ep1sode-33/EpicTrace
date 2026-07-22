// UI 基线:会话(有消息)/ 空态 hero / Composer 聚焦 / 树行 / 空项目。
import { chromium } from 'playwright-core'

const out = process.argv[2] ?? '/tmp/epictrace-shots'
const tag = process.argv[3] ?? 'base'
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 820 } })
const errors = []
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console.error: ${m.text()}`) })

await page.goto('http://localhost:5199', { waitUntil: 'networkidle' })

// 会话视图(带消息 + composer)
await page.getByRole('button', { name: '项目与对话' }).click()
await page.waitForTimeout(500)
const item = page.getByText('物理页号简介').first()
if (await item.count()) { await item.click(); await page.waitForTimeout(800) }
await page.screenshot({ path: `${out}/${tag}-1-conversation.png` })

// Composer 聚焦态
await page.locator('textarea[aria-label="对话输入"]').click()
await page.locator('textarea[aria-label="对话输入"]').fill('帮我总结一下这个项目里 PPN 的要点')
await page.waitForTimeout(200)
await page.screenshot({ path: `${out}/${tag}-2-composer.png` })

// 空态 hero(新会话)
await page.getByRole('button', { name: '新建对话' }).first().click().catch(() => {})
await page.waitForTimeout(600)
await page.screenshot({ path: `${out}/${tag}-3-empty.png` })

console.log(JSON.stringify({ errors }, null, 2))
await browser.close()
