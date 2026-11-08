const cheerio = require('cheerio')
function httpUrl(value) {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('仅支持不含凭证的 HTTP/HTTPS 地址')
  return url.toString().replace(/\/$/, '')
}
function payload(binary, kind, source) {
  if (kind === 'image') return {data: binary, name: 'collected.png'}
  if (kind !== 'text') throw new Error('未知采集类型')
  const $ = cheerio.load(binary.toString('utf8'))
  $('script,style,noscript,nav,footer').remove()
  const text = $('body').text().replace(/\s+/g, ' ').trim()
  if (!text) throw new Error('没有提取到文本，请选择可直接访问的静态页面')
  return {data: Buffer.from(JSON.stringify({text, source_url: source}) + '\n'), name: 'collected.jsonl'}
}
async function collect(value, kind, fetchSource = fetch) {
  const source = httpUrl(value)
  const response = await fetchSource(source, {redirect: 'error', signal: AbortSignal.timeout(30000)})
  if (!response.ok) throw new Error('来源请求失败：' + response.status)
  const chunks = []; let size = 0
  for await (const chunk of response.body) {
    size += chunk.byteLength
    if (size > 20 * 1024 * 1024) throw new Error('资料超过 20 MB')
    chunks.push(chunk)
  }
  return {...payload(Buffer.concat(chunks), kind, source), source}
}
module.exports = {httpUrl, payload, collect}
