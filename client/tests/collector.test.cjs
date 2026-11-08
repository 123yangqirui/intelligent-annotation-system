const {test}=require('node:test')
const assert=require('node:assert/strict')
const {httpUrl,payload,collect}=require('../collector.cjs')
test('Only credential-free HTTP sources are accepted',()=>{
  for(const value of ['file:///tmp/test','javascript:alert(1)','http://user:secret@localhost'])assert.throws(()=>httpUrl(value))
  assert.equal(httpUrl('https://example.com/'),'https://example.com')
})
test('Text extraction drops scripts/navigation and records source',()=>{
  const result=payload(Buffer.from('<nav>菜单</nav><body><p>需要退款</p><script>private()</script></body>'),'text','http://example.com')
  const row=JSON.parse(result.data);assert.equal(row.text,'需要退款');assert.equal(row.source_url,'http://example.com');assert.equal(result.name,'collected.jsonl')
})
test('Empty text is not imported',()=>assert.throws(()=>payload(Buffer.from('<script>only()</script>'),'text','http://example.com'),/没有提取/))
test('Image bytes are preserved',()=>{const data=Buffer.from([1,2,3]);assert.deepEqual(payload(data,'image','http://example.com').data,data)})
test('Network response contract uses redirect rejection',async()=>{
  const result=await collect('http://example.com','text',async(url,options)=>{assert.equal(options.redirect,'error');return {ok:true,body:[Buffer.from('<p>资料</p>')]}})
  assert.equal(JSON.parse(result.data).text,'资料')
  await assert.rejects(collect('http://example.com','text',async()=>({ok:false,status:403})),/403/)
})
test('Oversized streams stop before import',async()=>{
  await assert.rejects(collect('http://example.com','image',async()=>({ok:true,body:[Buffer.alloc(21*1024*1024)]})),/20 MB/)
})
